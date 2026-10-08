"""Exercise the redesigned UI with local demo data; never contact the library."""
import json
import logging
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from werkzeug.serving import make_server
from seat_service import DemoClient, SeatService, SettingsStore
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
            search.clear()
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
            free_seat = '.seat-cell[aria-label="1열람실 A 3번 빈자리 상세 보기"]'
            click(free_seat)
            visible('#seat-sheet')
            assert service.snapshot()['reservation'] is None, 'inspection created a booking'
            screenshot('ios-seat-sheet')
            driver.switch_to.active_element.send_keys(Keys.ESCAPE)
            wait.until(EC.invisibility_of_element_located((By.ID, 'seat-sheet')))
            assert driver.switch_to.active_element.get_attribute('aria-label') == '1열람실 A 3번 빈자리 상세 보기'
            click(free_seat)
            click('#quick-reserve-button')
            visible('#confirm-dialog')
            assert service.snapshot()['reservation'] is None
            started = time.monotonic()
            click('#confirm-dialog .primary')
            visible('#reservation')
            assert time.monotonic() - started < 8, 'assignment fell back to slow idle polling'
            wait.until(lambda d: d.execute_script('return document.activeElement.id') == 'reservation')
            assert 'NFC' in visible('#reservation-badge').text
            assert visible('#repeat-toggle').get_attribute('aria-checked') == 'true'
            screenshot('ios-my-seat')

            # Repeat controls preserve the held seat; background repeat preserves navigation.
            old_id = service.snapshot()['reservation']['id']
            click('#repeat-toggle')
            wait.until(lambda d: visible('#repeat-toggle').get_attribute('aria-checked') == 'false')
            assert service.snapshot()['reservation']['id'] == old_id
            click('#repeat-toggle')
            click('#confirm-dialog .primary')
            wait.until(lambda d: visible('#repeat-toggle').get_attribute('aria-checked') == 'true')
            tab('find')
            visible('.mini-bar')
            service._update(repeat={**service.snapshot()['repeat'], 'dueAt': time.time() + 2})
            service.wake.set()
            wait.until(lambda d: (current := service.snapshot()['reservation']) and current['id'] != old_id)
            wait.until(lambda d: '자동 재예약 완료' in visible('#toast').text)
            assert driver.find_element(By.ID, 'nav-find').get_attribute('aria-current') == 'page'
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
            query('3')
            click('.browse-toolbar button')
            target = '.seat-cell[aria-label="2열람실 3번 빈자리 대기 선택"]'
            click(target)
            click('#selection-summary')
            visible('.selected-list')
            assert '2열람실 · 3번' in visible('.selected-list').text
            for width in (320, 390):
                mobile(width)
                no_overflow()
                screenshot(f'ios-selected-{width}')
            click('.browse-toolbar button')
            # Stop leaves the previous target as a draft; inspect the explicitly chosen free seat.
            free_row = driver.find_element(By.XPATH, '//button[contains(@class,"seat-list-button")][.//strong[normalize-space(.)="2열람실 · 3번"]]')
            driver.execute_script('arguments[0].scrollIntoView({block:"center"})', free_row)
            free_row.click()
            click('#quick-reserve-button')
            screenshot('ios-switch-confirm')
            click('#confirm-dialog .primary')
            wait.until(lambda d: (current := service.snapshot()['reservation']) and current['seatId'] == 232003)
            wait.until(lambda d: '2열람실' in visible('#reservation-seat').text)
            assert service.snapshot()['repeat'], 'switch did not enable repeat'

            # Simulate a server-reported NFC confirmation only in DemoClient.
            with service.operation:
                client.current['state'] = 'CHARGE'
                client.current['endTime'] = '18:30'
            service.tick()
            click('#refresh')
            wait.until(lambda d: visible('#reservation-badge').text == '배정 확정')
            assert not service.snapshot()['repeat'], 'confirmed assignment kept repeating'
            assert visible('#release').text == '좌석 반납'
            screenshot('ios-confirmed')
            click('#release')
            click('#confirm-dialog .primary')
            wait.until(EC.invisibility_of_element_located((By.ID, 'reservation')))
            assert service.snapshot()['reservation'] is None

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
            errors = [entry for entry in driver.get_log('browser') if entry['level'] == 'SEVERE']
            assert not errors, json.dumps(errors)
            print('PASS: 320/390px and desktop layout, 16px search, tab persistence, sheet focus, booking, repeat without navigation jump, live wait additions/removals, held-seat waiting, multiple selection, switching, confirmed state, release; no browser errors')
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
