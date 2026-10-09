"""Exercise the redesigned UI with local demo data; never contact the library."""
import json
import logging
import sys
import tempfile
import threading
import time
from pathlib import Path
from datetime import datetime
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from werkzeug.serving import make_server
from seat_service import DemoClient, KST, LibraryError, SeatService, SettingsStore, schedule_window
from webapp import create_app


def main():
    artifacts = Path('test-artifacts')
    artifacts.mkdir(exist_ok=True)
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    with tempfile.TemporaryDirectory() as directory:
        client = DemoClient()
        service = SeatService(SettingsStore(Path(directory) / 'state.db'), client=client, demo=True)
        service.tick()
        service.start()
        app = create_app(service, 'demo-password-for-ui-test', secure_cookie=False)
        server = make_server('127.0.0.1', 0, app, threaded=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        options = webdriver.ChromeOptions()
        options.add_argument('--headless=new')
        options.add_argument('--disable-gpu')
        options.set_capability('goog:loggingPrefs', {'browser': 'ALL'})
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        wait = WebDriverWait(driver, 15)

        def visible(selector):
            return wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, selector)))

        def click(selector):
            element = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, selector)))
            driver.execute_script('arguments[0].scrollIntoView({block:"center"})', element)
            element.click()

        def tab(name):
            click('#nav-' + name)
            wait.until(lambda d: d.find_element(By.ID, 'nav-' + name).get_attribute('aria-current') == 'page')

        def query(value):
            search = visible('#seat-search')
            # WebDriver clear() can miss React's input event; use actual keystrokes.
            search.send_keys(Keys.CONTROL, 'a')
            search.send_keys(Keys.BACKSPACE)
            if value:
                search.send_keys(value)
            search.send_keys(Keys.ENTER)

        def screenshot(name):
            wait.until(lambda d: d.execute_script('return document.getAnimations().every(a => a.playState === "finished")'))
            driver.save_screenshot(str(artifacts / (name + '.png')))

        def mobile(width=390):
            driver.execute_cdp_cmd('Emulation.setDeviceMetricsOverride', {
                'width': width, 'height': 844, 'deviceScaleFactor': 1, 'mobile': True})

        def no_overflow():
            assert driver.execute_script('return document.documentElement.scrollWidth <= window.innerWidth'), 'horizontal overflow'

        try:
            mobile()
            driver.get(f'http://127.0.0.1:{server.server_port}')
            visible('#access-password').send_keys('demo-password-for-ui-test')
            click('#access-form button')
            visible('.seat-cell')
            no_overflow()
            assert driver.execute_script('return getComputedStyle(document.querySelector("#seat-search")).fontSize') == '16px'
            screenshot('ios-find')
            # Room overview, scoped search and native history keep selection context.
            click('[data-view="all"]')
            visible('#room-overview')
            assert len(driver.find_elements(By.CSS_SELECTOR, '.room-card')) == 6
            assert not driver.find_elements(By.CSS_SELECTOR, '.seat-cell')
            for width in (320, 390):
                mobile(width)
                no_overflow()
                screenshot(f'ios-room-overview-{width}')
            click('#room-card-234')
            visible('#room-detail-heading')
            assert visible('#room-detail-heading').text == '3열람실 B'
            assert len(driver.find_elements(By.CSS_SELECTOR, '.room-group')) == 1
            assert driver.execute_script('return document.activeElement.id') == 'room-detail-heading'
            query('3')
            screenshot('ios-room-seats')
            click('#back-to-rooms')
            visible('#room-overview')
            assert driver.execute_script('return document.activeElement.id') == 'room-card-234'
            driver.forward()
            visible('#room-detail-heading')
            assert visible('#seat-search').get_attribute('value') == '3'
            tab('my')
            driver.back()
            visible('#room-overview')
            assert driver.find_element(By.ID, 'nav-find').get_attribute('aria-current') == 'page'
            assert driver.execute_script('return document.activeElement.id') == 'room-card-234'
            click('[data-view="single"]')
            free_seat = '.seat-cell[aria-label="1열람실 A 3번 빈자리 상세 보기"]'
            click(free_seat)
            visible('#seat-sheet')
            assert service.snapshot()['reservation'] is None, 'inspection created a booking'
            screenshot('ios-seat-sheet')
            driver.switch_to.active_element.send_keys(Keys.ESCAPE)
            wait.until(EC.invisibility_of_element_located((By.ID, 'seat-sheet')))
            assert driver.switch_to.active_element.get_attribute('aria-label') == '1열람실 A 3번 빈자리 상세 보기'
            click(free_seat)
            started = time.monotonic()
            click('#quick-reserve-button')
            visible('#reservation')
            assert time.monotonic() - started < 8, 'assignment fell back to slow idle polling'
            wait.until(lambda d: d.execute_script('return document.activeElement.id') == 'reservation')
            assert visible('#reservation-badge').text == '배정 확정'
            assert not driver.find_elements(By.ID, 'repeat-toggle')
            assert not service.snapshot()['running'] and not service.snapshot()['repeat']
            screenshot('ios-my-seat')

            tab('find')
            visible('.mini-bar')
            screenshot('ios-mini-bar')

            # Filters and search survive tab changes.
            query('1')
            tab('settings')
            visible('#panel-settings')
            screenshot('ios-settings')
            tab('find')
            assert visible('#seat-search').get_attribute('value') == '1'
            target = '.seat-cell[aria-label="2열람실 1번 8분 상세 보기"]'
            click(target)
            click('#quick-reserve-button')
            held_id = service.snapshot()['reservation']['id']
            click('#confirm-dialog .primary')
            visible('#wait-status')
            assert driver.execute_script('return document.querySelector("#wait-status").getBoundingClientRect().top') < 280, 'active wait is hidden below current assignment'
            assert service.snapshot()['running']
            assert service.snapshot()['reservation']['id'] == held_id, 'waiting released held seat'
            driver.execute_script('window.scrollTo(0, 0)')
            screenshot('ios-switch-wait')
            # Extend a running job in place, then remove just the added target.
            click('#add-wait-seats')
            query('2')
            click('.seat-cell[aria-label="2열람실 2번 15분 상세 보기"]')
            assert visible('#update-wait-seat').text == '대기에 추가'
            screenshot('ios-add-to-wait')
            click('#update-wait-seat')
            wait.until(lambda d: visible('#selected-tab-count').text == '2')
            assert service.snapshot()['targets'] == ['232:1', '232:2']
            assert service.snapshot()['reservation']['id'] == held_id
            click('[data-view="selected"]')
            no_overflow()
            screenshot('ios-live-wait-list')
            click('.selected-list button[aria-label="2열람실 2번 대기에서 제외"]')
            wait.until(lambda d: visible('#selected-tab-count').text == '1')
            assert service.snapshot()['running']
            assert service.snapshot()['targets'] == ['232:1']
            assert service.snapshot()['reservation']['id'] == held_id
            tab('my')
            click('#stop-wait')
            wait.until(lambda d: not service.snapshot()['running'])

            # Multiple selection is explicit and ordered, separate from inspecting a seat.
            tab('find')
            query('31')
            click('.browse-toolbar button')
            target = '.seat-cell[aria-label^="2열람실 31번 "][aria-label$="대기 선택"]'
            click(target)
            click('#selection-summary')
            visible('.selected-list')
            assert '2열람실 · 31번' in visible('.selected-list').text
            for width in (320, 390):
                mobile(width)
                no_overflow()
                screenshot(f'ios-selected-{width}')
            click('.browse-toolbar button')
            click('[data-view="all"]')
            query('3')
            click('.seat-cell[aria-label="2열람실 3번 빈자리 상세 보기"]')
            click('#quick-reserve-button')
            screenshot('ios-switch-confirm')
            click('#confirm-dialog .primary')
            wait.until(lambda d: (current := service.snapshot()['reservation']) and current['seatId'] == 232003)
            wait.until(lambda d: '2열람실' in visible('#reservation-seat').text)
            assert not service.snapshot()['repeat']
            wait.until(lambda d: visible('#reservation-badge').text == '배정 확정')
            assert '배정 확정 완료' in visible('#toast').text
            assert not service.snapshot()['repeat'], 'confirmed assignment kept repeating'
            assert visible('#release').text == '좌석 반납'
            screenshot('ios-confirmed')
            # Renew the demo seat, then exercise quota exhaustion under an open-hours clock.
            with service.operation:
                client.current.update(endTime=datetime.fromtimestamp(time.time() + 7100, KST).strftime('%Y-%m-%d %H:%M:%S'),
                                      renewableAt=time.time() - 100)
                service.tick()
            driver.execute_script('window.dispatchEvent(new Event("focus"))')
            wait.until(lambda d: visible('#renew-seat').is_enabled())
            assert '3 / 3' in visible('#renewal-count').text
            screenshot('ios-renewal-controls')
            click('#renew-seat')
            click('#confirm-dialog .primary')
            wait.until(lambda d: '2 / 3' in visible('#renewal-count').text)
            assert '연장 완료' in visible('#toast').text
            click('#auto-renew-toggle')
            assert '남은 횟수가 0이면' in visible('#confirm-dialog').text
            screenshot('ios-auto-renew-confirmation')
            click('#confirm-dialog .primary')
            wait.until(lambda d: visible('#auto-renew-toggle').get_attribute('aria-checked') == 'true')
            prior_renewal_id = service.snapshot()['reservation']['id']
            with patch('seat_service.closed_until', return_value=None):
                with service.operation:
                    client.current.update(renewableCnt=0, renewableAt=time.time() - 100,
                        endTime=datetime.fromtimestamp(time.time() + 7100, KST).strftime('%Y-%m-%d %H:%M:%S'))
                    service.tick()
            driver.execute_script('window.dispatchEvent(new Event("focus"))')
            wait.until(lambda d: '3 / 3' in visible('#renewal-count').text)
            assert service.snapshot()['reservation']['id'] != prior_renewal_id
            assert service.snapshot()['reservation']['state'] == 'CHARGE'
            assert service.snapshot()['autoRenew']['reservationId'] == service.snapshot()['reservation']['id']
            assert visible('#auto-renew-toggle').get_attribute('aria-checked') == 'true'
            no_overflow()
            screenshot('ios-auto-renew-reassigned')
            # Return and reassign through one API call, including automatic confirmation.
            original = service.snapshot()['reservation']
            assert driver.execute_script('return document.querySelector("#reassign").nextElementSibling.id') == 'release'
            click('#reassign')
            assert '자리를 잃을 수 있습니다' in visible('#confirm-dialog').text
            screenshot('ios-reassign-confirmation')
            click('#confirm-dialog .secondary')
            assert service.snapshot()['reservation']['id'] == original['id']
            click('#reassign')
            click('#confirm-dialog .primary')
            wait.until(lambda d: (current := service.snapshot()['reservation']) and current['id'] != original['id'] and current['state'] == 'CHARGE')
            wait.until(lambda d: '재배정·확정 완료' in visible('#toast').text)
            reassigned = service.snapshot()['reservation']
            assert reassigned['id'] != original['id']
            assert reassigned['seatId'] == original['seatId']
            assert reassigned['state'] == 'CHARGE'
            assert not service.snapshot()['repeat'] and not service.snapshot()['running']
            no_overflow()
            screenshot('ios-reassigned')
            # A contested target first restores and confirms the original seat, then
            # a later vacancy switches and confirms automatically. Demo requests only.
            base_reserve, base_seats = client.reserve, client.seats
            contention = {'rejected': False, 'occupied': True}
            def contested_reserve(seat_id):
                if seat_id == 102006 and not contention['rejected']:
                    contention.update(rejected=True, occupied=True)
                    raise LibraryError('데모: 다른 이용자가 먼저 예약했습니다.')
                base_reserve(seat_id)
            def contested_seats(room_id):
                rows = base_seats(room_id)
                for row in rows:
                    if row['id'] == 102006 and contention['occupied']:
                        row['isOccupied'] = True
                return rows
            client.reserve, client.seats = contested_reserve, contested_seats
            with service.operation:
                service.tick()
            tab('find')
            driver.execute_script('window.dispatchEvent(new Event("focus"))')
            click('[data-view="all"]')
            query('6')
            click('.browse-toolbar button')
            click('.seat-cell[aria-label^="1열람실 A 6번 "][aria-label$="대기 선택"]')
            click('#start-stop')
            assert '새 좌석과 복구 좌석 모두 자동으로 배정확정' in visible('#confirm-dialog').text
            screenshot('ios-auto-confirm-wait-dialog')
            click('#confirm-dialog .primary')
            with service.operation:
                contention['occupied'] = False
            service.wake.set()
            wait.until(lambda d: (current := service.snapshot()['reservation']) and current['id'] != reassigned['id'] and current['state'] == 'CHARGE')
            recovered = service.snapshot()
            assert recovered['reservation']['seatId'] == reassigned['seatId']
            assert recovered['running'] and not recovered['repeat']
            # A poll may observe the intermediate TEMP_CHARGE state, in which case
            # the final notice is "배정 확정 완료" rather than "재배정·확정 완료".
            wait.until(lambda d: visible('#reservation-badge').text == '배정 확정'
                       and '확정 완료' in visible('#reservation-result').text)
            visible('#wait-status')
            screenshot('ios-recovered-confirmed-wait')
            with service.operation:
                contention['occupied'] = False
            service.wake.set()
            wait.until(lambda d: (current := service.snapshot()['reservation']) and current['seatId'] == 102006 and current['state'] == 'CHARGE')
            wait.until(lambda d: '1열람실 A' in visible('#reservation-seat').text and '배정 확정 완료' in visible('#toast').text)
            assert not service.snapshot()['running'] and not service.snapshot()['repeat']
            no_overflow()
            screenshot('ios-switched-confirmed')
            client.reserve, client.seats = base_reserve, base_seats
            click('#release')
            click('#confirm-dialog .primary')
            wait.until(EC.invisibility_of_element_located((By.ID, 'reservation')))
            assert service.snapshot()['reservation'] is None

            # Register/cancel through the real UI, then execute the persisted demo job
            # with only the service clock advanced. No provider requests are made.
            tab('schedule')
            fixed_evening = datetime(2026, 10, 9, 20, tzinfo=KST).timestamp()
            fixed_morning = datetime(2026, 10, 10, 5, tzinfo=KST).timestamp()
            with patch('seat_service.schedule_window', return_value=schedule_window(fixed_evening)):
                driver.refresh()
                tab('schedule')
                Select(visible('select[aria-label="시간 예약 열람실"]')).select_by_value('102')
                Select(visible('select[aria-label="시간 예약 좌석 번호"]')).select_by_value('102:3')
                Select(visible('select[aria-label="예약 시간"]')).select_by_value('06:20')
                for width in (320, 390):
                    mobile(width)
                    no_overflow()
                    screenshot(f'ios-schedule-form-{width}')
                click('.schedule-form button[type="submit"]')
                wait.until(lambda d: service.snapshot()['scheduledBooking'] is not None)
                visible('.schedule-summary')
                assert service.snapshot()['reservation'] is None
                screenshot('ios-schedule-pending')
                click('.schedule-summary .destructive')
                wait.until(lambda d: service.snapshot()['scheduledBooking']['status'] == 'cancelled')
                assert service.snapshot()['reservation'] is None
                service.set_schedule('102:3', fixed_morning)
            with service.operation, patch('seat_service.time', wraps=time) as service_clock:
                service_clock.time.return_value = fixed_morning
                service.tick()
                state = service.snapshot()
                assert state['scheduledBooking']['status'] == 'succeeded'
                assert state['reservation']['state'] == 'CHARGE' and state['autoRenew']
            driver.refresh()
            tab('schedule')
            wait.until(lambda d: '시간 예약 완료' in visible('.schedule-summary').text)
            screenshot('ios-schedule-success')
            with service.operation:
                current = service.snapshot()['reservation']
                service.release(current['id'], current['state'])
            tab('settings')
            visible('#notification-heading')
            no_overflow()
            screenshot('ios-notification-settings')

            tab('find')
            query('')
            click('[data-view="single"]')
            for width in (320, 390):
                mobile(width)
                driver.execute_script('window.scrollTo(0, 0)')
                no_overflow()
                assert visible('.seat-cell').rect['height'] <= 100
                screenshot(f'ios-find-{width}')
            driver.execute_cdp_cmd('Emulation.clearDeviceMetricsOverride', {})
            driver.set_window_size(1280, 1000)
            driver.execute_script('window.scrollTo(0, 0)')
            no_overflow()
            screenshot('desktop')
            click('[data-view="all"]')
            visible('#room-overview')
            no_overflow()
            screenshot('desktop-room-overview')
            errors = [entry for entry in driver.get_log('browser') if entry['level'] == 'SEVERE']
            assert not errors, json.dumps(errors)
            print('PASS: mobile/desktop layout, rooms, search, immediate booking+confirmation, retired repeat controls, waiting, switching, renewal, quota reset with continued auto-renewal, return-reassign-confirm, recovery+confirmation, scheduling, release; no browser errors')
        except Exception:
            screenshot('failure')
            raise
        finally:
            driver.quit()
            server.shutdown()
            server.server_close()
            service.stop()


if __name__ == '__main__':
    main()
