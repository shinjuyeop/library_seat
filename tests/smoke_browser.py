"""Run the complete demo UI at mobile and desktop widths without touching the library."""
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from werkzeug.serving import make_server

from seat_service import DemoClient, SeatService, SettingsStore
from webapp import create_app


def main():
    artifacts = Path('test-artifacts')
    artifacts.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        service = SeatService(SettingsStore(Path(directory) / 'state.db'), client=DemoClient(), demo=True)
        service.tick()
        service.start()
        app = create_app(service, 'demo-password-for-ui-test', secure_cookie=False)
        server = make_server('127.0.0.1', 0, app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        options = webdriver.ChromeOptions()
        options.add_argument('--headless=new')
        options.add_argument('--disable-gpu')
        options.set_capability('goog:loggingPrefs', {'browser':'ALL'})
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        try:
            driver.execute_cdp_cmd('Emulation.setDeviceMetricsOverride', {'width':390,'height':844,'deviceScaleFactor':1,'mobile':True})
            driver.get(f'http://127.0.0.1:{server.server_port}')
            wait = WebDriverWait(driver, 15)
            wait.until(EC.visibility_of_element_located((By.ID, 'access-password'))).send_keys('demo-password-for-ui-test')
            driver.find_element(By.CSS_SELECTOR, '#access-form button').click()
            wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, '.seat-select')))
            assert driver.execute_script('return document.documentElement.scrollWidth <= window.innerWidth'), 'mobile overflow'
            driver.save_screenshot(str(artifacts / 'mobile.png'))
            seat = driver.find_element(By.CSS_SELECTOR, '.seat-select[aria-label="1열람실 A 3번 빈자리 대기 선택"]')
            driver.execute_script('arguments[0].scrollIntoView({block:"center"})', seat)
            seat.click()
            assert '1개' in driver.find_element(By.ID, 'selection-count').text
            reservation_requested_at = time.monotonic()
            driver.find_element(By.ID, 'start-stop').click()
            wait.until(EC.visibility_of_element_located((By.ID, 'reservation')))
            assert time.monotonic() - reservation_requested_at < 8, 'assignment fell back to slow idle polling'
            assert 'NFC' in driver.find_element(By.ID, 'reservation-badge').text
            assert not service.snapshot()['running']
            wait.until(EC.text_to_be_present_in_element((By.ID, 'reservation-result'), '배정 완료'))
            wait.until(lambda browser: browser.execute_script('return document.activeElement.id') == 'reservation')
            wait.until(lambda browser: browser.find_element(By.ID, 'reservation').rect['y'] - browser.execute_script('return window.scrollY') < 40)
            assert driver.find_element(By.CSS_SELECTOR, '.action-bar button').text == '내 좌석 보기'
            driver.save_screenshot(str(artifacts / 'mobile-assigned.png'))
            wait.until(lambda browser: browser.find_element(By.ID, 'repeat-toggle').get_attribute('aria-checked') == 'true')
            # Default auto repeat may be disabled without giving up the held seat.
            driver.find_element(By.ID, 'repeat-toggle').click()
            wait.until(lambda browser: browser.find_element(By.ID, 'repeat-toggle').get_attribute('aria-checked') == 'false')
            driver.find_element(By.ID, 'repeat-toggle').click()
            wait.until(EC.visibility_of_element_located((By.ID, 'confirm-dialog')))
            driver.find_element(By.CSS_SELECTOR, '#confirm-dialog .primary').click()
            wait.until(lambda browser: browser.find_element(By.ID, 'repeat-toggle').get_attribute('aria-checked') == 'true')
            old_id = service.snapshot()['reservation']['id']
            # Advance only the demo's repeat deadline; never contact the library.
            service._update(repeat={**service.snapshot()['repeat'], 'dueAt': time.time() + 2})
            service.wake.set()
            driver.execute_script('window.scrollTo(0,document.body.scrollHeight)')
            wait.until(lambda browser: service.snapshot()['reservation'] and service.snapshot()['reservation']['id'] != old_id)
            wait.until(EC.text_to_be_present_in_element((By.ID, 'reservation-result'), '자동 재예약 완료'))
            wait.until(lambda browser: browser.find_element(By.ID, 'reservation').rect['y'] - browser.execute_script('return window.scrollY') < 40)
            driver.save_screenshot(str(artifacts / 'mobile-repeated.png'))
            driver.find_element(By.CSS_SELECTOR, '[data-view="all"]').click()
            search = driver.find_element(By.ID, 'seat-search')
            search.send_keys('1')
            target = driver.find_element(By.CSS_SELECTOR, '.seat-cell[aria-label="2열람실 1번 8분 대기 선택"]')
            driver.execute_script('arguments[0].scrollIntoView({block:"center"})', target)
            target.click()
            held_id = service.snapshot()['reservation']['id']
            driver.find_element(By.ID, 'start-stop').click()
            wait.until(EC.visibility_of_element_located((By.ID, 'confirm-dialog')))
            driver.find_element(By.CSS_SELECTOR, '#confirm-dialog .primary').click()
            wait.until(lambda browser: service.snapshot()['running'])
            assert service.snapshot()['reservation']['id'] == held_id, 'wait released held seat'
            wait.until(EC.visibility_of_element_located((By.ID, 'wait-status')))
            driver.save_screenshot(str(artifacts / 'mobile-switch-wait.png'))
            driver.find_element(By.ID, 'start-stop').click()
            wait.until(lambda browser: not service.snapshot()['running'])
            wait.until(lambda browser: browser.find_element(By.ID, 'start-stop').text == '자동 예약 시작')
            search.clear()
            search.send_keys('3')
            target = driver.find_element(By.CSS_SELECTOR, '.seat-cell[aria-label="2열람실 3번 빈자리 대기 선택"]')
            driver.execute_script('arguments[0].scrollIntoView({block:"center"})', target)
            target.click()
            assert driver.execute_script('return document.documentElement.scrollWidth <= window.innerWidth'), 'switch action bar overflow'
            driver.save_screenshot(str(artifacts / 'mobile-switch-selected.png'))
            driver.find_element(By.ID, 'quick-reserve-button').click()
            wait.until(EC.visibility_of_element_located((By.ID, 'confirm-dialog')))
            driver.find_element(By.CSS_SELECTOR, '#confirm-dialog .primary').click()
            wait.until(lambda browser: service.snapshot()['reservation']['seatId'] == 232003)
            wait.until(EC.text_to_be_present_in_element((By.ID, 'reservation-seat'), '2열람실'))
            assert service.snapshot()['repeat'], 'switch did not enable repeat'
            driver.save_screenshot(str(artifacts / 'mobile-switched.png'))
            release = driver.find_element(By.ID, 'release')
            driver.execute_script('arguments[0].scrollIntoView({block:"center"})', release)
            release.click()
            wait.until(EC.visibility_of_element_located((By.ID, 'confirm-dialog')))
            driver.find_element(By.CSS_SELECTOR, '#confirm-dialog .primary').click()
            wait.until(EC.invisibility_of_element_located((By.ID, 'reservation')))
            driver.find_element(By.ID, 'clear-search').click()
            driver.find_element(By.CSS_SELECTOR, '[data-view="single"]').click()
            for width in (320, 390):
                driver.execute_cdp_cmd('Emulation.setDeviceMetricsOverride', {'width':width,'height':844,'deviceScaleFactor':1,'mobile':True})
                driver.execute_script('window.scrollTo(0,0)')
                assert driver.execute_script('return document.documentElement.scrollWidth <= window.innerWidth'), f'{width}px overflow'
                assert driver.find_element(By.CSS_SELECTOR, '.seat-select').rect['height'] <= 100, 'single-seat card too tall'
                driver.save_screenshot(str(artifacts / f'mobile-{width}.png'))
            driver.execute_cdp_cmd('Emulation.clearDeviceMetricsOverride', {})
            driver.set_window_size(1280, 1000)
            driver.execute_script('window.scrollTo(0,0)')
            driver.save_screenshot(str(artifacts / 'desktop.png'))
            errors = [entry for entry in driver.get_log('browser') if entry['level'] == 'SEVERE']
            assert not errors, json.dumps(errors)
            print('PASS: mobile/desktop layout, login, automatic assignment and repeat, manual repeat disable, held-seat wait, immediate switch, NFC status, cancel; no browser errors')
        finally:
            driver.quit()
            server.shutdown()
            server.server_close()
            service.stop()


if __name__ == '__main__':
    main()
