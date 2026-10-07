"""Browser login shared by the desktop and server applications."""
import json
import os
import re
import time
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from library_config import MY_RESERVATION_PAGE_URL

def get_credentials_from_env():
    env_user = os.getenv("KONKUK_LIBRARY_ID")
    env_pass = os.getenv("KONKUK_LIBRARY_PW")
    if env_user and env_pass:
        print("✅ 환경 변수에서 계정 정보를 불러왔습니다.")
        return env_user, env_pass

    print("⚠ 환경 변수가 설정되지 않아 자동 로그인을 건너뜁니다.")
    print("   - KONKUK_LIBRARY_ID")
    print("   - KONKUK_LIBRARY_PW")
    return None, None


def _find_login_input_fields(driver):
    try:
        inputs = driver.find_elements(By.XPATH, "//input[not(@type='hidden')]")
    except Exception:
        return None, None

    id_field = None
    pw_field = None

    for elem in inputs:
        try:
            field_type = (elem.get_attribute("type") or "").lower()
            field_name = (elem.get_attribute("name") or "").lower()
            field_id = (elem.get_attribute("id") or "").lower()
            placeholder = (elem.get_attribute("placeholder") or "").lower()
            aria_label = (elem.get_attribute("aria-label") or "").lower()
            meta = " ".join([field_name, field_id, placeholder, aria_label])

            if pw_field is None and field_type == "password":
                pw_field = elem
                continue

            if id_field is None and field_type in ["text", "email", "", "tel", "number"]:
                if any(keyword in meta for keyword in ["id", "user", "login", "학번", "아이디", "username"]):
                    id_field = elem
        except Exception:
            continue

    if id_field is None:
        for elem in inputs:
            try:
                field_type = (elem.get_attribute("type") or "").lower()
                if field_type in ["text", "email", "", "tel", "number"]:
                    id_field = elem
                    break
            except Exception:
                continue

    return id_field, pw_field


def _attempt_auto_login(driver, username, password):
    if not username or not password:
        return False

    try:
        for _ in range(15):
            id_field, pw_field = _find_login_input_fields(driver)
            if id_field and pw_field:
                break
            time.sleep(1)
        else:
            print("⚠ 로그인 입력창을 찾지 못했습니다. 수동 로그인으로 진행합니다.")
            return False

        id_field.clear()
        id_field.send_keys(username)
        pw_field.clear()
        pw_field.send_keys(password)

        # 로그인 제출은 페이지 구조별로 실패할 수 있어 여러 방식으로 재시도
        submit_xpaths = [
            "//button[@type='submit']",
            "//input[@type='submit']",
            "//button[contains(normalize-space(.), '로그인')]",
            "//a[contains(normalize-space(.), '로그인')]",
            "//*[contains(@class, 'login') and (self::button or self::a)]"
        ]

        submitted = False
        for _ in range(3):
            submit_candidates = []
            for xpath in submit_xpaths:
                try:
                    submit_candidates.extend(driver.find_elements(By.XPATH, xpath))
                except Exception:
                    continue

            for submit in submit_candidates:
                try:
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", submit)
                except Exception:
                    pass

                try:
                    submit.click()
                    submitted = True
                    break
                except Exception:
                    try:
                        driver.execute_script("arguments[0].click();", submit)
                        submitted = True
                        break
                    except Exception:
                        continue

            if submitted:
                break

            try:
                form = pw_field.find_element(By.XPATH, "ancestor::form[1]")
                driver.execute_script("arguments[0].submit();", form)
                submitted = True
                break
            except Exception:
                pass

            try:
                pw_field.send_keys("\n")
                submitted = True
                break
            except Exception:
                pass

            time.sleep(0.7)

        if not submitted:
            print("⚠ 자동 로그인 제출 버튼 클릭에 실패했습니다. 수동 로그인으로 진행합니다.")
            return False

        print("✅ 자동 로그인 시도 완료. 토큰 발생을 감시합니다...")
        return True
    except Exception:
        print("자동 로그인 중 오류가 발생했습니다. 공식 페이지에서 다시 로그인해 주세요.")
        return False


def _extract_token_from_performance_logs(driver):
    try:
        logs = driver.get_log('performance')
    except Exception:
        return None

    for entry in logs:
        try:
            message = json.loads(entry['message'])['message']
            method = message.get('method')
            params = message.get('params', {})

            if method == 'Network.requestWillBeSent':
                headers = params.get('request', {}).get('headers', {})
                token = headers.get('Pyxis-Auth-Token') or headers.get('pyxis-auth-token')
                if token:
                    return token

            if method == 'Network.responseReceivedExtraInfo':
                headers = params.get('headers', {})
                token = headers.get('Pyxis-Auth-Token') or headers.get('pyxis-auth-token')
                if token:
                    return token
        except Exception:
            continue

    return None


def _extract_token_from_cookies(driver):
    try:
        cookies = driver.get_cookies()
    except Exception:
        return None

    for cookie in cookies:
        name = cookie.get('name', '')
        if name.lower() == 'pyxis-auth-token':
            return cookie.get('value')
    return None


def _parse_my_reservation_from_text(text):
    if not text:
        return None

    raw_text = text
    cleaned_text = re.sub(r"<[^>]+>", " ", text)
    cleaned_text = re.sub(r"\s+", " ", cleaned_text).strip()

    seat_match = None
    seat_patterns = [
        r"(제\s*\d+\s*열람실\s*(?:\([^)]+\)|[A-Z가-힣])?\s*\d+\s*번)",
        r"(\d+\s*열람실\s*(?:\([^)]+\)|[A-Z가-힣])?\s*\d+\s*번)",
        r"(열람실\s*(?:\([^)]+\)|[A-Z가-힣])?\s*\d+\s*번)",
    ]

    for pattern in seat_patterns:
        seat_match = re.search(pattern, cleaned_text)
        if seat_match:
            break

    if not seat_match:
        html_like = re.sub(r"\s+", " ", raw_text)
        seat_match = re.search(
            r"제\s*\d+\s*열람실(?:.|\n){0,80}?\d+\s*번",
            html_like,
            re.IGNORECASE
        )
    reserve_time_match = re.search(r"예약일시\s*([오전오후0-9:\s~\-]+)", cleaned_text)
    remaining_match = re.search(r"잔여시간\s*([0-9]+\s*/\s*[0-9]+)", cleaned_text)
    extendable_match = re.search(r"연장가능시간\s*([오전오후0-9:\s]+)", cleaned_text)
    extension_match = re.search(r"연장\s*([0-9]+\s*/\s*[0-9]+)", cleaned_text)
    assignment_type_match = re.search(r"배정(?:구분|유형)?\s*[:：]?\s*(임시배정|일반배정|배정)", cleaned_text)

    if not assignment_type_match:
        # 페이지 문구가 단순할 수 있어 임시배정 키워드는 단독 탐지
        if "임시배정" in cleaned_text:
            assignment_type_match = re.search(r"(임시배정)", cleaned_text)

    reservation = {}
    if seat_match:
        seat_value = seat_match.group(1).strip() if seat_match.lastindex else seat_match.group(0).strip()
        seat_value = re.sub(r"\s+", " ", seat_value)
        reservation["seatDisplay"] = seat_value
    if reserve_time_match:
        reservation["reservationDisplay"] = reserve_time_match.group(1).strip()
    if remaining_match:
        reservation["remainingDisplay"] = remaining_match.group(1).strip()
    if extendable_match:
        reservation["extendableDisplay"] = extendable_match.group(1).strip()
    if extension_match:
        reservation["extensionDisplay"] = extension_match.group(1).strip()
    if assignment_type_match:
        reservation["assignmentTypeDisplay"] = assignment_type_match.group(1).strip()

    return reservation or None


def get_token_automatically(username=None, password=None, *, headless=False):
    """
    브라우저의 네트워크 로그(Network Tab)를 직접 뒤져서
    'Pyxis-Auth-Token' 헤더가 전송되는 순간을 포착하는 함수
    """
    print("🌍 브라우저를 실행합니다... 로그인을 진행해주세요.")
    
    options = webdriver.ChromeOptions()
    if headless:
        if not username or not password:
            return None, None, None
        options.add_argument('--headless=new')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--window-size=1280,900')
    if os.getenv('CHROME_BINARY'):
        options.binary_location = os.environ['CHROME_BINARY']
    options.add_experimental_option('excludeSwitches', ['enable-logging'])
    # 성능 로깅 활성화
    options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
    
    driver = None
    try:
        driver_path = os.getenv('CHROMEDRIVER_PATH') or ChromeDriverManager().install()
        driver = webdriver.Chrome(service=Service(driver_path), options=options)
        driver.set_page_load_timeout(30)
        # 건국대 도서관 로그인 페이지로 이동
        driver.get("https://library.konkuk.ac.kr/login")
        
        found_token = None

        auto_login_started = _attempt_auto_login(driver, username, password)
        if headless and not auto_login_started:
            return None, None, None
        if not auto_login_started:
            print("⏳ 수동 로그인 대기 중... (네트워크를 감시하고 있습니다)")
        else:
            print("⏳ 자동 로그인 후 토큰 대기 중... (네트워크를 감시하고 있습니다)")
        
        # 최대 5분 동안 감시
        for i in range(60 if headless else 300):
            if i % 5 == 0:
                print(".", end="", flush=True) # 진행상황 표시
            
            time.sleep(1)
            
            found_token = _extract_token_from_performance_logs(driver)
            if found_token:
                print("\n로그인 토큰을 확보했습니다.")
                break
            
            if found_token:
                break
                
            # [보조 수단] 쿠키에서도 한번 찾아봄
            if not found_token:
                found_token = _extract_token_from_cookies(driver)
                if found_token:
                    print("\n로그인 토큰을 확보했습니다.")
                    break
            
            if found_token:
                break
        
        if not found_token:
            print("\n❌ 토큰을 찾지 못했습니다. 로그인이 완료되었는지 확인해주세요.")
            return None, None, None

        reservation_snapshot = None
        cookie_dict = {}
        try:
            # 초기 예약 현황은 1회만 확인하고, 좌석이 없어도 즉시 GUI로 진행
            driver.get(MY_RESERVATION_PAGE_URL)
            time.sleep(1)
            reservation_snapshot = _parse_my_reservation_from_text(driver.page_source)

            for cookie in driver.get_cookies():
                name = cookie.get("name")
                value = cookie.get("value")
                if name and value:
                    cookie_dict[name] = value

            if reservation_snapshot:
                print("✅ 초기 예약 현황 스냅샷을 가져왔습니다.")
        except Exception:
            reservation_snapshot = None

        return found_token, reservation_snapshot, cookie_dict

    except Exception:
        print("\n브라우저 로그인에 실패했습니다. 계정과 Chrome 설치 상태를 확인하세요.")
        return None, None, None
    finally:
        try:
            driver.quit()
        except:
            pass


