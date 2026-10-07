"""Run the complete demo UI at mobile and desktop widths without touching the library."""
import json
import sys
import tempfile
import threading
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
            seat = driver.find_element(By.CSS_SELECTOR, '.seat-select[aria-label^="3번 "]')
            driver.execute_script('arguments[0].scrollIntoView({block:"center"})', seat)
            seat.click()
            assert '1개' in driver.find_element(By.ID, 'selection-count').text
            driver.find_element(By.ID, 'start-stop').click()
            wait.until(EC.visibility_of_element_located((By.ID, 'reservation')))
            assert 'NFC' in driver.find_element(By.ID, 'reservation-badge').text
            assert not service.snapshot()['running']
            release = driver.find_element(By.ID, 'release')
            driver.execute_script('arguments[0].scrollIntoView({block:"center"})', release)
            release.click()
            wait.until(EC.visibility_of_element_located((By.ID, 'confirm-dialog')))
            driver.find_element(By.CSS_SELECTOR, '#confirm-dialog [value="confirm"]').click()
            wait.until(EC.invisibility_of_element_located((By.ID, 'reservation')))
            driver.execute_cdp_cmd('Emulation.clearDeviceMetricsOverride', {})
            driver.set_window_size(1280, 1000)
            driver.execute_script('window.scrollTo(0,0)')
            driver.save_screenshot(str(artifacts / 'desktop.png'))
            errors = [entry for entry in driver.get_log('browser') if entry['level'] == 'SEVERE']
            assert not errors, json.dumps(errors)
            print('PASS: mobile layout, login, selection, server auto-reservation, NFC status, cancel, desktop layout; no browser errors')
        finally:
            driver.quit()
            server.shutdown()
            server.server_close()
            service.stop()


if __name__ == '__main__':
    main()
