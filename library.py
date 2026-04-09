import tkinter as tk
from tkinter import ttk, messagebox
import requests
import datetime
import threading
import time
import json
import os
import re
import random
import math

# === 자동화를 위한 라이브러리 ===
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities
from selenium.webdriver.common.by import By

# ================= CONSTANTS =================
URLS = {
    102: "https://library.konkuk.ac.kr/pyxis-api/1/api/rooms/102/seats",
    101: "https://library.konkuk.ac.kr/pyxis-api/1/api/rooms/101/seats"
}

MY_RESERVATION_API_CANDIDATES = [
    "https://library.konkuk.ac.kr/pyxis-api/1/api/mylibrary/seat/reservations",
    "https://library.konkuk.ac.kr/pyxis-api/1/api/mylibrary/seat/reservation",
    "https://library.konkuk.ac.kr/pyxis-api/1/api/mylibrary/seat/reservations",
    "https://library.konkuk.ac.kr/pyxis-api/1/api/seat-charges",
    "https://library.konkuk.ac.kr/pyxis-api/1/api/seat-charges/current",
    "https://library.konkuk.ac.kr/pyxis-api/1/api/my-library/seat/reservations",
    "https://library.konkuk.ac.kr/pyxis-api/1/api/my-library/seat/reservation",
    "https://library.konkuk.ac.kr/pyxis-api/1/api/my-library/seat/reservations"
]

MY_RESERVATION_PAGE_URL = "https://library.konkuk.ac.kr/mylibrary/seat/reservations"

WATCH_LIST = [
    # === 제 1열람실 A (102호) ===
    (102, "1"), (102, "2"), (102, "3"), (102, "4"),
    (102, "239"), 
    (102, "391"), (102, "392"), (102, "393"), 
    (102, "394"), (102, "395"), (102, "396"),
    
    # === 제 1열람실 B (101호) ===
    (101, "21"), (101, "22"), (101, "23"),
    (101, "310"), 
    (101, "397"), (101, "398"), 
    (101, "405"), (101, "406"), (101, "407"), (101, "408")
]

BASE_REFRESH_SECONDS = 60
WAIT_ACTIVE_REFRESH_SECONDS = 30
FAST_REFRESH_SECONDS = 5
FAST_TRACKING_THRESHOLD_MINUTES = 1
ZERO_MINUTE_REFRESH_SECONDS = 2
ONE_MINUTE_REFRESH_SECONDS = 10
TEMP_REPEAT_THRESHOLD_SECONDS = 9 * 60
# =============================================

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
    except Exception as e:
        print(f"⚠ 자동 로그인 중 오류가 발생했습니다: {e}")
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


def get_token_automatically(username=None, password=None):
    """
    브라우저의 네트워크 로그(Network Tab)를 직접 뒤져서
    'Pyxis-Auth-Token' 헤더가 전송되는 순간을 포착하는 함수
    """
    print("🌍 브라우저를 실행합니다... 로그인을 진행해주세요.")
    
    # [중요] 네트워크 로그를 캡처하기 위한 설정
    caps = DesiredCapabilities.CHROME
    caps['goog:loggingPrefs'] = {'performance': 'ALL'}

    options = webdriver.ChromeOptions()
    options.add_experimental_option('excludeSwitches', ['enable-logging'])
    # 성능 로깅 활성화
    options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
    
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    
    try:
        # 건국대 도서관 로그인 페이지로 이동
        driver.get("https://library.konkuk.ac.kr/login")
        
        found_token = None

        auto_login_started = _attempt_auto_login(driver, username, password)
        if not auto_login_started:
            print("⏳ 수동 로그인 대기 중... (네트워크를 감시하고 있습니다)")
        else:
            print("⏳ 자동 로그인 후 토큰 대기 중... (네트워크를 감시하고 있습니다)")
        
        # 최대 5분 동안 감시
        for i in range(300):
            if i % 5 == 0:
                print(".", end="", flush=True) # 진행상황 표시
            
            time.sleep(1)
            
            found_token = _extract_token_from_performance_logs(driver)
            if found_token:
                print(f"\n🎉 [네트워크 로그에서 발견] 토큰 찾음! : {found_token[:10]}...")
                break
            
            if found_token:
                break
                
            # [보조 수단] 쿠키에서도 한번 찾아봄
            if not found_token:
                found_token = _extract_token_from_cookies(driver)
                if found_token:
                    print(f"\n🎉 [쿠키에서 발견] 토큰 찾음! : {found_token[:10]}...")
                    break
            
            if found_token:
                break
        
        if not found_token:
            print("\n❌ 토큰을 찾지 못했습니다. 로그인이 완료되었는지 확인해주세요.")
            return None, None

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

    except Exception as e:
        print(f"\n브라우저 실행 중 오류: {e}")
        return None, None, None
    finally:
        try:
            driver.quit()
        except:
            pass


class SeatMonitorApp:
    def __init__(self, root, token, username=None, password=None, initial_reservation=None, web_cookies=None):
        self.root = root
        self.root.title("도서관 괜찮은 좌석 모니터")
        self.root.geometry("1200x750")
        self.root.resizable(True, True)
        self.root.configure(bg="white")
        
        self.auth_token = token
        self.username = username
        self.password = password
        self._reservation_cache_text = None
        self._reservation_cache_ts = 0
        self.initial_reservation = initial_reservation
        self.last_reservation = initial_reservation
        self.current_reservation = initial_reservation
        self.current_reservation_item = None
        self.cleared_after_return = False

        # TEMP_CHARGE 자동 반복(취소 후 재예약) 상태
        self.temp_repeat_enabled = False
        self.temp_repeat_worker_running = False
        self.temp_repeat_lock = threading.Lock()
        self.temp_repeat_last_action_ts = 0
        self.temp_repeat_started_ts = 0
        self.temp_repeat_autostart_reservation_id = None

        # === 자동 예약 대기 상태 저장소 ===
        self.latest_status_map = {}   # key: (room_id, seat_no) -> latest seat data
        self.reserve_buttons = {}     # key: (room_id, seat_no) -> action button
        self.auto_wait_targets = {}   # key: (room_id, seat_no)
        # value: {enabled, last_seen_occupied}
        self.auto_wait_lock = threading.Lock()
        self.next_refresh_seconds = BASE_REFRESH_SECONDS
        self.scheduled_refresh_seconds = BASE_REFRESH_SECONDS
        self.seat_end_time_cache = {}  # key: (room_id, seat_no) -> {remaining_minutes, end_time_ts}

        self.web_cookies = web_cookies or {}
        self.web_session = requests.Session()
        if self.web_cookies:
            self.web_session.cookies.update(self.web_cookies)
        self.setup_styles()
        
        main_frame = ttk.Frame(root, padding="20")
        main_frame.pack(fill=tk.BOTH, expand=True)

        top_frame = ttk.Frame(main_frame)
        top_frame.pack(fill=tk.X, pady=(0, 15))

        title_label = ttk.Label(top_frame, text="좌석 현황 모니터", style="Title.TLabel")
        title_label.pack(side=tk.LEFT)

        refresh_btn = ttk.Button(top_frame, text="🔄 새로고침", command=self.manual_refresh)
        refresh_btn = ttk.Button(top_frame, text="🔄 새로고침", command=self.manual_refresh, style="Primary.TButton")
        refresh_btn.pack(side=tk.RIGHT)

        self.seat_status_labels = {}
        rooms_row = ttk.Frame(main_frame)
        rooms_row.pack(fill=tk.BOTH, expand=True)

        left_col = ttk.Frame(rooms_row)
        left_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))

        right_col = ttk.Frame(rooms_row)
        right_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))

        self.create_seat_widgets(left_col, 102, "제 1열람실 (A)")
        self.create_seat_widgets(right_col, 101, "제 1열람실 (B)")

        my_reservation_frame = ttk.LabelFrame(main_frame, text="내 좌석 예약 현황", style="Group.TLabelframe", padding="10")
        my_reservation_frame.pack(fill=tk.X, pady=(8, 5))

        reservation_top_frame = ttk.Frame(my_reservation_frame)
        reservation_top_frame.pack(fill=tk.X)

        self.my_reservation_label = ttk.Label(
            reservation_top_frame,
            text="대기 중...",
            style="Seat.TLabel",
            justify=tk.LEFT,
            anchor="w"
        )
        self.my_reservation_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))

        self.release_button = ttk.Button(
            reservation_top_frame,
            text="반납",
            width=12,
            command=self.on_release_click,
            style="Danger.TButton"
        )
        self.release_button.pack(side=tk.RIGHT)
        self.release_button.config(state=tk.DISABLED)

        self.repeat_button = ttk.Button(
            reservation_top_frame,
            text="반복",
            width=12,
            command=self.on_repeat_click
        )

        self.regrab_button = ttk.Button(
            reservation_top_frame,
            text="다시 잡기",
            width=12,
            command=self.on_regrab_click
        )

        self.repeat_countdown_label = ttk.Label(
            reservation_top_frame,
            text="다음 반복까지: -",
            font=("맑은 고딕", 9),
            foreground="gray"
        )

        # 하단 상태 표시줄 구분을 위한 시각적 구분선 추가
        separator = ttk.Separator(main_frame, orient='horizontal')
        separator.pack(fill=tk.X, pady=(15, 10))

        bottom_frame = ttk.Frame(main_frame)
        bottom_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(10, 0))
        bottom_frame.pack(side=tk.BOTTOM, fill=tk.X)

        self.status_label = ttk.Label(bottom_frame, text="준비 완료", font=("맑은 고딕", 9), foreground="gray")
        self.status_label.pack(side=tk.LEFT)

        self.next_refresh_countdown_label = ttk.Label(
            bottom_frame,
            text="다음 자동갱신: -초",
            font=("맑은 고딕", 9),
            foreground="gray"
        )
        self.next_refresh_countdown_label.pack(side=tk.RIGHT, padx=(0, 14))

        self.current_time_label = ttk.Label(bottom_frame, text="현재 시각: --:--:--", font=("맑은 고딕", 9), foreground="gray")
        self.current_time_label.pack(side=tk.RIGHT)
        
        self.timer_id = None
        self.clock_timer_id = None
        self.next_update_at_ts = None
        self.update_current_time_label()
        self.update_gui()

    def update_current_time_label(self):
        now_str = datetime.datetime.now().strftime("%H:%M:%S")
        self.current_time_label.config(text=f"현재 시각: {now_str}")

        if self.next_update_at_ts is None:
            self.next_refresh_countdown_label.config(text="다음 자동갱신: -초")
        else:
            remain_seconds = max(0, int(self.next_update_at_ts - time.time()))
            self.next_refresh_countdown_label.config(text=f"다음 자동갱신: {remain_seconds}초")

        self.update_repeat_countdown_label(self.current_reservation)

        self.clock_timer_id = self.root.after(1000, self.update_current_time_label)

    def setup_styles(self):
        style = ttk.Style()
        style.theme_use('clam')
        style.configure(".", background="white")
        style.configure("TFrame", background="white")
        style.configure("TLabel", background="white")
        style.configure("TLabelframe", background="white")
        style.configure("TLabelframe.Label", background="white")
        style.configure("Group.TLabelframe", background="white")
        style.configure("Title.TLabel", font=("맑은 고딕", 16, "bold"))
        style.configure("Group.TLabelframe.Label", font=("맑은 고딕", 11, "bold"), foreground="#333333")
        style.configure("Seat.TLabel", font=("맑은 고딕", 10))
        style.configure("Time.TLabel", font=("맑은 고딕", 10, "bold"), foreground="#0056b3")
        style.configure("Empty.TLabel", font=("맑은 고딕", 10), foreground="gray")
        
        # 전체적인 색상 톤과 폰트 크기 조정 (모던하고 세련된 느낌)
        style.configure("Title.TLabel", font=("맑은 고딕", 18, "bold"), foreground="#2c3e50", background="white")
        style.configure("Group.TLabelframe.Label", font=("맑은 고딕", 12, "bold"), foreground="#34495e", background="white")
        style.configure("Seat.TLabel", font=("맑은 고딕", 10), foreground="#2c3e50", background="white")
        style.configure("Time.TLabel", font=("맑은 고딕", 10, "bold"), foreground="#2980b9", background="white")
        style.configure("Empty.TLabel", font=("맑은 고딕", 10), foreground="#7f8c8d", background="white")
        
        # 버튼 스타일 세분화 및 패딩 추가 (버튼이 답답해 보이지 않도록 함)
        style.configure("TButton", font=("맑은 고딕", 10), padding=4)
        style.configure("Primary.TButton", font=("맑은 고딕", 10, "bold"), foreground="#27ae60")
        style.configure("Danger.TButton", font=("맑은 고딕", 10, "bold"), foreground="#c0392b")

    def create_seat_widgets(self, parent, room_id, room_name):
        group_frame = ttk.LabelFrame(parent, text=room_name, style="Group.TLabelframe", padding="10")
        group_frame.pack(fill=tk.X, pady=5)

        target_seats = [seat_no for r_id, seat_no in WATCH_LIST if r_id == room_id]

        for seat_no in target_seats:
            row_frame = ttk.Frame(group_frame)
            row_frame.pack(fill=tk.X, pady=3)

            seat_lbl = ttk.Label(row_frame, text=f"{seat_no}번:", style="Seat.TLabel", width=8)
            seat_lbl.pack(side=tk.LEFT)

            action_btn = ttk.Button(
                row_frame,
                text="예약 대기",
                width=14,
                command=lambda rid=room_id, sno=seat_no: self.on_action_button_click(rid, sno)
            )
            action_btn.pack(side=tk.RIGHT)
            self.reserve_buttons[(room_id, seat_no)] = action_btn

            info_frame = ttk.Frame(row_frame)
            info_frame.pack(side=tk.RIGHT, padx=(0, 8))

            status_lbl = ttk.Label(info_frame, text="대기 중...", style="Empty.TLabel", anchor="e")
            status_lbl.pack(anchor="e")
            
            self.seat_status_labels[(room_id, seat_no)] = status_lbl

    def _is_wait_enabled(self, key):
        with self.auto_wait_lock:
            return bool(self.auto_wait_targets.get(key, {}).get("enabled"))

    def on_action_button_click(self, room_id, seat_no):
        key = (room_id, seat_no)

        # 이미 예약 대기 등록된 좌석이면 감시 해제
        if self._is_wait_enabled(key):
            with self.auto_wait_lock:
                self.auto_wait_targets.pop(key, None)
            self.status_label.config(text=f"{seat_no}번 예약 대기 해제", foreground="gray")
            self.update_button_states()
            return

        seat_data = self.latest_status_map.get(key)
        if not seat_data:
            messagebox.showwarning("안내", "아직 좌석 상태를 받지 못했습니다. 잠시 후 다시 시도해주세요.")
            return

        # 좌석이 비어 있으면 즉시 예약 시도
        if not seat_data.get("isOccupied"):
            threading.Thread(
                target=self._manual_reserve_worker,
                args=(room_id, seat_no),
                daemon=True
            ).start()
            return

        # 좌석이 점유 중이면 즉시 예약 대기 등록
        with self.auto_wait_lock:
            self.auto_wait_targets[key] = {
                "enabled": True,
                "last_seen_occupied": True
            }

        self.status_label.config(text=f"{seat_no}번 예약 대기 등록", foreground="blue")
        self.update_button_states()

    def fetch_seat_detail(self, room_id, seat_no):
        # 해당 좌석 상세를 조회해서 seatId 확보
        url = URLS.get(room_id)
        if not url:
            return None

        data = self._request_json(url)
        seat_list = data.get('list', []) if isinstance(data, dict) else []
        for seat in seat_list:
            if str(seat.get('code')) == str(seat_no):
                return seat
        return None

    def _extract_seat_id(self, seat_data):
        if not isinstance(seat_data, dict):
            return None

        for key in ['seatId', 'id', 'seat_id', 'smufSeatId']:
            value = seat_data.get(key)
            if value not in [None, ""]:
                return value
        return None

    def try_reserve_seat(self, room_id, seat_id):
        return self.try_reserve_seat_by_id(seat_id, room_id)

    def try_reserve_seat_by_id(self, seat_id, room_id=None):
        url = "https://library.konkuk.ac.kr/pyxis-api/1/api/seat-charges"
        if room_id:
            referer = f"https://library.konkuk.ac.kr/library-services/smuf/reading-rooms/{room_id}"
        else:
            referer = MY_RESERVATION_PAGE_URL

        headers = self._build_headers(referer=referer, accept="application/json, text/plain, */*")
        headers["content-type"] = "application/json;charset=UTF-8"
        headers["origin"] = "https://library.konkuk.ac.kr"
        payload = {"seatId": seat_id, "smufMethodCode": "PC"}

        try:
            response = self.web_session.post(url, headers=headers, json=payload, timeout=7)

            content_type = (response.headers.get("Content-Type") or "").lower()
            response_text = response.text or ""

            # 로그인 페이지/오류 페이지 HTML이 반환되면 인증 실패로 처리
            if "text/html" in content_type or response_text.lstrip().lower().startswith("<html"):
                return False, "AUTH_OR_REDIRECT", "인증이 만료되었거나 웹 페이지로 리다이렉트되었습니다. 다시 로그인 후 시도해주세요."

            try:
                body = response.json()
            except Exception:
                return False, response.status_code, "예약 API 응답이 JSON이 아닙니다. 다시 로그인 후 시도해주세요."

            success = bool(body.get("success")) and response.status_code == 200
            code = body.get("code", response.status_code)
            message = body.get("message") or body.get("msg") or body.get("error") or "요청 실패"
            return success, code, message
        except Exception as e:
            return False, "REQUEST_ERROR", str(e)

    def _parse_datetime_value(self, value):
        if value in [None, "", []]:
            return None

        text = str(value).strip()
        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y.%m.%d %H:%M:%S",
            "%Y.%m.%d %H:%M"
        ]

        for fmt in formats:
            try:
                return datetime.datetime.strptime(text, fmt)
            except Exception:
                continue

        try:
            return datetime.datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            return None

    def _to_positive_int(self, value):
        if value in [None, "", []] or isinstance(value, bool):
            return None
        try:
            parsed = int(float(str(value).strip()))
            return parsed if parsed > 0 else None
        except Exception:
            return None

    def _extract_current_seat_id(self, reservation=None):
        item = self._extract_real_reservation_item(reservation)
        if not isinstance(item, dict):
            return None

        for key in ["seatId", "smufSeatId"]:
            parsed = self._to_positive_int(item.get(key))
            if parsed:
                return parsed

        seat_obj = item.get("seat")
        if isinstance(seat_obj, dict):
            for key in ["seatId", "smufSeatId", "id"]:
                parsed = self._to_positive_int(seat_obj.get(key))
                if parsed:
                    return parsed

        return None

    def _extract_current_room_id(self, reservation=None):
        item = self._extract_real_reservation_item(reservation)
        if not isinstance(item, dict):
            return None

        for key in ["roomId", "smufRoomId"]:
            parsed = self._to_positive_int(item.get(key))
            if parsed:
                return parsed

        room_obj = item.get("room")
        if isinstance(room_obj, dict):
            for key in ["id", "roomId", "code", "roomCode"]:
                value = room_obj.get(key)
                parsed = self._to_positive_int(value)
                if parsed:
                    return parsed

        return None

    def _extract_current_reservation_start_time(self, reservation=None):
        item = self._extract_real_reservation_item(reservation)
        if not isinstance(item, dict):
            return None

        start_time = self._extract_recursive_value(
            item,
            [
                "startTime", "startDateTime", "beginDateTime", "startDate",
                "chargeStartDateTime", "chargeStartTime", "seatChargeStartDateTime", "seatChargeStartTime",
                "useStartDateTime", "useStartTime", "fromDateTime"
            ]
        )
        return self._parse_datetime_value(start_time)

    def _extract_real_reservation_item(self, reservation=None):
        # 응답 구조(dict/list/data/list[0])가 달라도 실제 예약 item(dict)을 공통 추출
        if reservation is None and isinstance(self.current_reservation_item, dict):
            return self.current_reservation_item

        source = reservation if reservation is not None else self.current_reservation
        if source is None:
            source = self.last_reservation
        if source is None:
            return None

        item = self._extract_reservation_from_payload(source)
        if isinstance(item, dict):
            return item

        if isinstance(source, dict) and self._has_reservation_identity(source):
            return source

        return None

    def get_current_reservation_id(self, reservation=None):
        # 현재 예약 item의 id를 seatCharge 식별자로 사용
        item = self._extract_real_reservation_item(reservation)
        if not isinstance(item, dict):
            return None

        for key in ["id", "seatCharge", "seatChargeId", "seat_charge_id"]:
            value = item.get(key)
            if value in [None, "", []] or isinstance(value, bool):
                continue
            try:
                return int(float(str(value).strip()))
            except Exception:
                continue

        return None

    def get_current_reservation_state_code(self, reservation=None):
        # 상태 분기 기준: state.code (TEMP_CHARGE / CHARGE / IN_USE ...)
        item = self._extract_real_reservation_item(reservation)
        if not isinstance(item, dict):
            return None

        state = item.get("state")
        if isinstance(state, dict):
            state_code = state.get("code") or state.get("stateCode") or state.get("value")
            if state_code not in [None, "", []]:
                return str(state_code).strip().upper()

        for key in ["stateCode", "seatChargeStateCode", "statusCode", "chargeStateCode"]:
            value = item.get(key)
            if value not in [None, "", []]:
                return str(value).strip().upper()

        # 기존 배정 구분 판별 로직 재사용: 임시배정이면 TEMP_CHARGE로 해석
        assignment_type = self._extract_assignment_type_display(item)
        if assignment_type == "임시배정":
            return "TEMP_CHARGE"
        if assignment_type:
            return "CHARGE"

        return None

    def _build_release_headers(self):
        headers = self._build_headers(
            referer=MY_RESERVATION_PAGE_URL,
            accept="application/json, text/plain, */*"
        )
        headers["content-type"] = "application/json;charset=UTF-8"
        headers["origin"] = "https://library.konkuk.ac.kr"
        headers["pyxis-auth-token"] = self.auth_token
        return headers

    def _parse_release_api_response(self, response, api_name):
        content_type = (response.headers.get("Content-Type") or "").lower()
        response_text = response.text or ""
        if "text/html" in content_type or response_text.lstrip().lower().startswith("<html"):
            return False, "AUTH_OR_REDIRECT", "인증이 만료되었거나 웹 페이지로 리다이렉트되었습니다. 다시 로그인 후 시도해주세요."

        try:
            body = response.json()
        except Exception:
            return False, response.status_code, f"{api_name} API 응답이 JSON이 아닙니다. 다시 로그인 후 시도해주세요."

        success = bool(body.get("success")) and response.status_code == 200
        code = body.get("code", response.status_code)
        message = body.get("message") or body.get("msg") or body.get("error") or "요청 실패"
        return success, code, message

    def try_cancel_temp_reservation(self, reservation_id):
        # TEMP_CHARGE(임시배정)는 실제 입실 전 상태이므로 seat-charges delete(_method=delete)로 취소
        url = f"https://library.konkuk.ac.kr/pyxis-api/1/api/seat-charges/{reservation_id}"
        headers = self._build_release_headers()

        try:
            response = self.web_session.post(
                url,
                headers=headers,
                params={"smufMethodCode": "PC", "_method": "delete"},
                timeout=7
            )
            return self._parse_release_api_response(response, "예약 취소")
        except Exception as e:
            return False, "REQUEST_ERROR", str(e)

    def try_return_seat(self, reservation_id):
        # CHARGE/IN_USE 등 실제 사용 상태는 seat-discharges로 반납 처리
        url = "https://library.konkuk.ac.kr/pyxis-api/1/api/seat-discharges"
        headers = self._build_release_headers()
        payload = {"seatCharge": reservation_id, "smufMethodCode": "PC"}

        try:
            response = self.web_session.post(url, headers=headers, json=payload, timeout=7)
            return self._parse_release_api_response(response, "반납")
        except Exception as e:
            return False, "REQUEST_ERROR", str(e)

    def on_release_click(self):
        reservation_id = self.get_current_reservation_id()
        state_code = self.get_current_reservation_state_code()

        if not reservation_id:
            messagebox.showwarning("안내", "현재 해제 가능한 예약 정보를 찾지 못했습니다.")
            return

        is_temp_charge = state_code == "TEMP_CHARGE"
        confirm_title = "예약 취소 확인" if is_temp_charge else "반납 확인"
        confirm_message = "현재 임시 예약을 취소할까요?" if is_temp_charge else "현재 좌석을 반납할까요?"
        confirmed = messagebox.askyesno(confirm_title, confirm_message)
        if not confirmed:
            return

        threading.Thread(
            target=self._release_worker,
            args=(reservation_id, state_code),
            daemon=True
        ).start()

    def _release_worker(self, reservation_id, state_code):
        is_temp_charge = state_code == "TEMP_CHARGE"
        if is_temp_charge:
            success, code, message = self.try_cancel_temp_reservation(reservation_id)
        else:
            success, code, message = self.try_return_seat(reservation_id)

        if success:
            # 해제 성공 시 현재 예약 캐시를 비워 즉시 반영
            self._set_temp_repeat_enabled(False)
            self.temp_repeat_autostart_reservation_id = None
            self.cleared_after_return = True
            self.current_reservation_item = None
            self.current_reservation = None
            self.last_reservation = None
            self.initial_reservation = None

            success_title = "예약 취소 성공" if is_temp_charge else "반납 성공"
            success_message = "임시 예약이 취소되었습니다." if is_temp_charge else "좌석 반납이 완료되었습니다."
            self.root.after(0, lambda t=success_title, m=success_message: messagebox.showinfo(t, m))
            self.root.after(0, self.manual_refresh)
        else:
            fail_title = "예약 취소 실패" if is_temp_charge else "반납 실패"
            self.root.after(0, lambda t=fail_title, c=code, m=message: messagebox.showerror(t, f"code={c}\nmessage={m}"))

    def _is_temp_repeat_enabled(self):
        with self.temp_repeat_lock:
            return bool(self.temp_repeat_enabled)

    def _set_temp_repeat_enabled(self, enabled):
        with self.temp_repeat_lock:
            self.temp_repeat_enabled = bool(enabled)
            if not self.temp_repeat_enabled:
                self.temp_repeat_started_ts = 0

    def _clear_all_auto_wait_targets(self):
        with self.auto_wait_lock:
            has_targets = bool(self.auto_wait_targets)
            self.auto_wait_targets.clear()
        if has_targets:
            self.root.after(0, self.update_button_states)

    def _auto_enable_temp_repeat_for_current_reservation(self, reservation=None):
        reservation_id = self.get_current_reservation_id(reservation)
        state_code = self.get_current_reservation_state_code(reservation)

        if not reservation_id or state_code != "TEMP_CHARGE":
            self.temp_repeat_autostart_reservation_id = None
            return

        if self.temp_repeat_autostart_reservation_id == reservation_id:
            return

        self.temp_repeat_autostart_reservation_id = reservation_id
        if self._is_temp_repeat_enabled():
            return

        self._set_temp_repeat_enabled(True)
        self.temp_repeat_last_action_ts = 0
        self.temp_repeat_started_ts = time.time()
        self._ensure_temp_repeat_worker()

    def _ensure_temp_repeat_worker(self):
        with self.temp_repeat_lock:
            if self.temp_repeat_worker_running:
                return
            self.temp_repeat_worker_running = True

        threading.Thread(target=self._temp_repeat_worker_loop, daemon=True).start()

    def on_repeat_click(self):
        state_code = self.get_current_reservation_state_code()
        if state_code != "TEMP_CHARGE":
            messagebox.showwarning("안내", "반복 기능은 임시배정 상태에서만 사용할 수 있습니다.")
            return

        currently_enabled = self._is_temp_repeat_enabled()
        self._set_temp_repeat_enabled(not currently_enabled)

        if currently_enabled:
            self.temp_repeat_autostart_reservation_id = self.get_current_reservation_id()
            self.status_label.config(text="임시배정 반복 기능을 중지했습니다.", foreground="gray")
            self.update_release_button_state(self.current_reservation)
            return

        self.temp_repeat_autostart_reservation_id = self.get_current_reservation_id()
        self.temp_repeat_last_action_ts = 0
        self.temp_repeat_started_ts = time.time()
        self.status_label.config(text="임시배정 반복 기능을 시작했습니다. 9분 경과 시 자동 재배정합니다.", foreground="blue")
        self.update_release_button_state(self.current_reservation)
        self._ensure_temp_repeat_worker()

    def on_regrab_click(self):
        state_code = self.get_current_reservation_state_code()
        if state_code != "TEMP_CHARGE":
            messagebox.showwarning("안내", "다시 잡기는 임시배정 상태에서만 사용할 수 있습니다.")
            return

        confirmed = messagebox.askyesno("다시 잡기 확인", "지금 임시배정을 취소하고 같은 좌석을 다시 잡을까요?")
        if not confirmed:
            return

        threading.Thread(target=self._regrab_worker, daemon=True).start()

    def _regrab_worker(self):
        item = self._extract_real_reservation_item()
        if not isinstance(item, dict):
            self.root.after(0, lambda: messagebox.showwarning("안내", "현재 임시배정 정보를 찾지 못했습니다."))
            return

        self._run_temp_repeat_cycle_with_options(item, show_success_popup=True, show_error_popup=True)

    def _temp_repeat_worker_loop(self):
        try:
            while self._is_temp_repeat_enabled():
                item = self._extract_real_reservation_item()
                state_code = self.get_current_reservation_state_code(item)

                if state_code != "TEMP_CHARGE":
                    self._set_temp_repeat_enabled(False)
                    self.root.after(0, lambda: self.status_label.config(text="임시배정 상태가 아니어서 반복을 중지했습니다.", foreground="gray"))
                    break

                start_dt = self._extract_current_reservation_start_time(item)
                if start_dt is not None:
                    elapsed_seconds = int((datetime.datetime.now() - start_dt).total_seconds())
                else:
                    # API에 시작시각이 없을 때는 반복 시작 시각을 기준으로 9분 카운트
                    if self.temp_repeat_started_ts <= 0:
                        self.temp_repeat_started_ts = time.time()
                    elapsed_seconds = int(time.time() - self.temp_repeat_started_ts)

                cooldown_ok = (time.time() - self.temp_repeat_last_action_ts) >= 30

                if elapsed_seconds >= TEMP_REPEAT_THRESHOLD_SECONDS and cooldown_ok:
                    self.temp_repeat_last_action_ts = time.time()
                    self._run_temp_repeat_cycle(item)

                time.sleep(2)
        finally:
            with self.temp_repeat_lock:
                self.temp_repeat_worker_running = False
            self.root.after(0, lambda: self.update_release_button_state(self.current_reservation))

    def _get_temp_repeat_remaining_seconds(self, reservation=None):
        if not self._is_temp_repeat_enabled():
            return None

        state_code = self.get_current_reservation_state_code(reservation)
        if state_code != "TEMP_CHARGE":
            return None

        start_dt = self._extract_current_reservation_start_time(reservation)
        if start_dt is not None:
            elapsed_seconds = max(0, int((datetime.datetime.now() - start_dt).total_seconds()))
        elif self.temp_repeat_started_ts > 0:
            elapsed_seconds = max(0, int(time.time() - self.temp_repeat_started_ts))
        else:
            return None

        return max(0, TEMP_REPEAT_THRESHOLD_SECONDS - elapsed_seconds)

    def update_repeat_countdown_label(self, reservation=None):
        remain_seconds = self._get_temp_repeat_remaining_seconds(reservation)
        if remain_seconds is None:
            self.repeat_countdown_label.config(text="다음 반복까지: -")
            return

        minutes, seconds = divmod(remain_seconds, 60)
        self.repeat_countdown_label.config(text=f"다음 반복까지: {minutes}분 {seconds}초")

    def _run_temp_repeat_cycle(self, reservation_item):
        return self._run_temp_repeat_cycle_with_options(
            reservation_item,
            show_success_popup=False,
            show_error_popup=False
        )

    def _run_temp_repeat_cycle_with_options(self, reservation_item, show_success_popup=False, show_error_popup=False):
        reservation_id = self.get_current_reservation_id(reservation_item)
        seat_id = self._extract_current_seat_id(reservation_item)
        room_id = self._extract_current_room_id(reservation_item)

        if not reservation_id or not seat_id:
            self._set_temp_repeat_enabled(False)
            if show_error_popup:
                self.root.after(0, lambda: messagebox.showerror("다시 잡기 실패", "반복에 필요한 예약/좌석 정보를 찾지 못했습니다."))
            else:
                self.root.after(0, lambda: messagebox.showerror("반복 중지", "반복에 필요한 예약/좌석 정보를 찾지 못해 중지합니다."))
            return False

        cancel_success, cancel_code, cancel_message = self.try_cancel_temp_reservation(reservation_id)
        if not cancel_success:
            if show_error_popup:
                self.root.after(0, lambda c=cancel_code, m=cancel_message: messagebox.showerror("다시 잡기 실패", f"code={c}\nmessage={m}"))
            else:
                self.root.after(0, lambda c=cancel_code, m=cancel_message: self.status_label.config(text=f"임시예약 취소 실패(code={c}): {m}", foreground="red"))
            return False

        reserve_success, reserve_code, reserve_message = self.try_reserve_seat_by_id(seat_id, room_id)
        if reserve_success:
            self.cleared_after_return = False
            self.temp_repeat_started_ts = time.time()
            self.root.after(0, lambda: self.status_label.config(text="임시배정을 자동으로 갱신했습니다.", foreground="green"))
            if show_success_popup:
                self.root.after(0, lambda: messagebox.showinfo("다시 잡기 성공", "임시배정을 취소 후 같은 좌석으로 다시 배정했습니다."))
            self.root.after(0, self.manual_refresh)
            return True

        if show_error_popup:
            self.root.after(0, lambda c=reserve_code, m=reserve_message: messagebox.showerror("다시 잡기 실패", f"code={c}\nmessage={m}"))
        else:
            self.root.after(0, lambda c=reserve_code, m=reserve_message: messagebox.showerror("반복 재예약 실패", f"code={c}\nmessage={m}"))
        return False

    def _manual_reserve_worker(self, room_id, seat_no):
        seat_data = self.latest_status_map.get((room_id, seat_no))
        if not seat_data:
            seat_data = self.fetch_seat_detail(room_id, seat_no)

        seat_id = self._extract_seat_id(seat_data)
        if not seat_id:
            detail = self.fetch_seat_detail(room_id, seat_no)
            seat_id = self._extract_seat_id(detail) if detail else None

        if not seat_id:
            self.root.after(0, lambda: messagebox.showerror("예약 실패", f"{seat_no}번 seatId 조회 실패"))
            return

        # 기존 예약이 있다면 먼저 해제 (갈아타기)
        current_res_id = self.get_current_reservation_id()
        if current_res_id:
            original_seat_id = self._extract_current_seat_id()
            original_room_id = self._extract_current_room_id()
            original_state_code = self.get_current_reservation_state_code()

            if not original_seat_id:
                self.root.after(0, lambda: messagebox.showerror("예약 실패", "기존 좌석 정보를 가져오지 못해 갈아타기를 중단합니다."))
                return

            if original_state_code == "TEMP_CHARGE":
                release_success, release_code, release_msg = self.try_cancel_temp_reservation(current_res_id)
            else:
                release_success, release_code, release_msg = self.try_return_seat(current_res_id)

            if not release_success:
                self.root.after(0, lambda c=release_code, m=release_msg: messagebox.showerror("예약 실패", f"기존 좌석 반납에 실패했습니다.\ncode={c}\nmessage={m}"))
                return

            time.sleep(0.5)  # 서버 반영 대기

            success, code, message = self.try_reserve_seat(room_id, seat_id)
            if success:
                self.cleared_after_return = False
                self._clear_all_auto_wait_targets()
                self.root.after(0, lambda: messagebox.showinfo("예약 성공", f"{seat_no}번 예약 성공"))
                self.root.after(0, self.manual_refresh)
            else:
                self.root.after(0, lambda c=code, m=message: messagebox.showwarning("예약 갈아타기 실패", f"새 좌석({seat_no}번) 예약에 실패하여 기존 좌석 재예약을 시도합니다.\n실패 사유: {m}"))
                re_reserve_success, re_code, re_message = self.try_reserve_seat_by_id(original_seat_id, original_room_id)
                if re_reserve_success:
                    self.root.after(0, lambda: messagebox.showinfo("재예약 성공", "기존 좌석을 다시 예약했습니다."))
                else:
                    self.root.after(0, lambda c=re_code, m=re_message: messagebox.showerror("치명적 오류", f"기존 좌석 재예약에도 실패했습니다. 직접 확인해주세요!\ncode={c}\nmessage={m}"))
                self.root.after(0, self.manual_refresh)
        else:
            success, code, message = self.try_reserve_seat(room_id, seat_id)
            if success:
                self.cleared_after_return = False
                self._clear_all_auto_wait_targets()
                self.root.after(0, lambda: messagebox.showinfo("예약 성공", f"{seat_no}번 예약 성공"))
                self.root.after(0, self.manual_refresh)
            else:
                self.root.after(0, lambda c=code, m=message: messagebox.showerror("예약 실패", f"code={c}\nmessage={m}"))

    def process_auto_wait(self, current_status):
        # 핵심: 내 예약 종료 시각이 아니라, 좌석 isOccupied 변화만으로 자동 예약 처리
        with self.auto_wait_lock:
            targets = list(self.auto_wait_targets.items())

        for key, target_info in targets:
            if not target_info.get("enabled"):
                continue

            room_id, seat_no = key
            if not self._is_wait_enabled(key):
                continue

            seat_data = current_status.get(key)
            if not seat_data:
                continue

            is_occupied = bool(seat_data.get("isOccupied"))

            with self.auto_wait_lock:
                if key in self.auto_wait_targets:
                    self.auto_wait_targets[key]["last_seen_occupied"] = is_occupied

            # 좌석이 풀리는 순간 자동 예약 시도
            if not is_occupied:
                seat_id = self._extract_seat_id(seat_data)
                if not seat_id:
                    detail = self.fetch_seat_detail(room_id, seat_no)
                    seat_id = self._extract_seat_id(detail) if detail else None

                if not seat_id:
                    print(f"[AUTO WAIT] {seat_no}번 seatId 조회 실패")
                    continue

                # 자동 예약 시 기존 예약이 있다면 먼저 해제 (갈아타기)
                current_res_id = self.get_current_reservation_id()
                if current_res_id:
                    original_seat_id = self._extract_current_seat_id()
                    original_room_id = self._extract_current_room_id()
                    original_state_code = self.get_current_reservation_state_code()

                    if not original_seat_id:
                        print(f"[AUTO WAIT] 기존 좌석 정보({current_res_id})를 가져오지 못해 갈아타기를 중단합니다.")
                        continue

                    print(f"[AUTO WAIT] 기존 예약({current_res_id}) 반납 시도 중...")
                    if original_state_code == "TEMP_CHARGE":
                        release_success, _, release_msg = self.try_cancel_temp_reservation(current_res_id)
                    else:
                        release_success, _, release_msg = self.try_return_seat(current_res_id)

                    if not release_success:
                        print(f"[AUTO WAIT] 기존 좌석 반납 실패: {release_msg}. 갈아타기를 중단합니다.")
                        continue

                    time.sleep(0.5)  # 서버 반영 대기

                    success, code, message = self.try_reserve_seat(room_id, seat_id)
                    if success:
                        self._clear_all_auto_wait_targets()
                        self.root.after(0, lambda s=seat_no: messagebox.showinfo("자동 예약 성공", f"{s}번 자동 예약 성공"))
                        self.root.after(0, self.manual_refresh)
                    else:
                        print(f"[AUTO WAIT] {seat_no}번 자동 예약 실패: code={code}, message={message}")
                        print(f"[AUTO WAIT] 기존 좌석({original_seat_id}) 재예약을 시도합니다...")
                        re_reserve_success, re_code, re_message = self.try_reserve_seat_by_id(original_seat_id, original_room_id)
                        if re_reserve_success:
                            self.root.after(0, lambda s=seat_no: messagebox.showwarning("예약 갈아타기 실패", f"{s}번 좌석 예약에 실패하여 기존 좌석을 다시 예약했습니다."))
                        else:
                            self.root.after(0, lambda s=seat_no, m=message, rm=re_message: messagebox.showerror("치명적 오류", f"새 좌석({s}번)과 기존 좌석 모두 예약에 실패했습니다.\n\n새 좌석 실패: {m}\n기존 좌석 실패: {rm}\n\n직접 예약 상태를 확인해주세요!"))
                        self.root.after(0, self.manual_refresh)
                else:
                    success, code, message = self.try_reserve_seat(room_id, seat_id)
                    if success:
                        self._clear_all_auto_wait_targets()
                        self.root.after(0, lambda s=seat_no: messagebox.showinfo("자동 예약 성공", f"{s}번 자동 예약 성공"))
                        self.root.after(0, self.manual_refresh)
                    else:
                        # 경쟁 실패 등은 감시 유지
                        print(f"[AUTO WAIT] {seat_no}번 자동 예약 실패: code={code}, message={message}")

    def update_button_states(self):
        # 버튼 상태를 좌석 점유/감시 상태에 따라 일괄 갱신
        for key, button in self.reserve_buttons.items():
            seat_data = self.latest_status_map.get(key)

            if self._is_wait_enabled(key):
                button.config(text="예약 대기 해제")
            elif seat_data and not seat_data.get("isOccupied"):
                button.config(text="예약")
            else:
                button.config(text="예약 대기")

    def _compute_refresh_interval_seconds(self, current_status):
        with self.auto_wait_lock:
            targets = list(self.auto_wait_targets.items())

        if not targets:
            return BASE_REFRESH_SECONDS

        has_one_minute_target = False

        for key, target_info in targets:
            if not target_info.get("enabled"):
                continue

            seat_data = current_status.get(key) if isinstance(current_status, dict) else None
            if not seat_data:
                seat_data = self.latest_status_map.get(key)
            if not seat_data:
                continue

            # 이미 빈 좌석이면 즉시 재시도 구간으로 간주
            if not seat_data.get("isOccupied"):
                return ZERO_MINUTE_REFRESH_SECONDS

            minutes_left = seat_data.get("remainingTime")
            if minutes_left is not None:
                try:
                    remain = float(minutes_left)
                    if remain <= 0:
                        return ZERO_MINUTE_REFRESH_SECONDS
                    if remain <= 1:
                        has_one_minute_target = True
                except Exception:
                    pass

        if has_one_minute_target:
            return ONE_MINUTE_REFRESH_SECONDS
        return WAIT_ACTIVE_REFRESH_SECONDS

    def _apply_refresh_jitter(self, base_seconds):
        # 고정 간격 패턴을 줄이기 위해 구간별 지터를 적용
        if base_seconds == ZERO_MINUTE_REFRESH_SECONDS:
            offset = random.randint(-1, 1)
            return max(1, base_seconds + offset)

        if base_seconds == ONE_MINUTE_REFRESH_SECONDS:
            offset = random.randint(-1, 1)
            return max(1, base_seconds + offset)

        if base_seconds >= 60:
            jitter_range = 6
        elif base_seconds >= 30:
            jitter_range = 3
        else:
            jitter_range = 1

        offset = random.randint(-jitter_range, jitter_range)
        return max(3, base_seconds + offset)

    def _compute_half_minute_aligned_delay_seconds(self):
        now = datetime.datetime.now()
        target = now.replace(microsecond=0)

        if now.second < 30:
            target = target.replace(second=30)
        else:
            target = (target + datetime.timedelta(minutes=1)).replace(second=30)

        target += datetime.timedelta(seconds=random.randint(0, 2))
        diff = (target - now).total_seconds()
        return max(1, math.ceil(diff))

    def manual_refresh(self):
        if self.timer_id:
            self.root.after_cancel(self.timer_id)
            self.timer_id = None
        self.next_update_at_ts = None
        self.next_refresh_countdown_label.config(text="다음 자동갱신: -초")
        self.update_gui()

    def update_gui(self):
        threading.Thread(target=self._update_logic, daemon=True).start()

    def _build_headers(self, referer=None, accept=None):
        return {
            "User-Agent": "Mozilla/5.0",
            "Pyxis-Auth-Token": self.auth_token,
            "pyxis-auth-token": self.auth_token,
            "Authorization": f"Bearer {self.auth_token}",
            "Referer": referer or "https://library.konkuk.ac.kr/",
            "Accept": accept or "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest"
        }

    def _build_api_referer(self, url):
        if "/api/seat-charges" in url:
            return MY_RESERVATION_PAGE_URL

        if "mylibrary/seat" in url or "my-library/seat" in url:
            return MY_RESERVATION_PAGE_URL

        if "/rooms/102/" in url:
            return "https://library.konkuk.ac.kr/library-services/smuf/reading-rooms/102"
        if "/rooms/101/" in url:
            return "https://library.konkuk.ac.kr/library-services/smuf/reading-rooms/101"

        return "https://library.konkuk.ac.kr/"

    def _request_json(self, url):
        try:
            response = self.web_session.get(
                url,
                headers=self._build_headers(referer=self._build_api_referer(url)),
                timeout=5
            )
            if response.status_code == 200:
                data = response.json()
                if data.get('success'):
                    return data.get('data')
        except Exception:
            pass
        return None

    def _request_json_flexible(self, url):
        # 예약 API는 엔드포인트마다 응답 구조가 달라질 수 있어 유연하게 파싱한다.
        try:
            response = self.web_session.get(
                url,
                headers=self._build_headers(referer=self._build_api_referer(url)),
                timeout=7
            )
            if response.status_code != 200:
                return None

            try:
                body = response.json()
            except Exception:
                return None

            if isinstance(body, dict):
                # 표준 구조: { success: bool, data: ... }
                if 'data' in body:
                    return body.get('data')
                # 비표준 구조: 바로 데이터가 내려오는 경우
                return body

            # 리스트 형태 응답도 허용
            if isinstance(body, list):
                return body
        except Exception:
            pass
        return None

    def _extract_reservation_from_payload(self, data):
        # 다양한 응답 구조에서 예약 정보 후보를 추출
        if data is None:
            return None

        candidates = [data]
        if isinstance(data, dict):
            for key in ['list', 'items', 'reservations', 'reservation', 'content', 'result', 'seatCharges']:
                value = data.get(key)
                if value not in [None, "", []]:
                    candidates.append(value)

        for candidate in candidates:
            reservation = self._find_reservation_item(candidate)
            if reservation and self._has_reservation_identity(reservation):
                return reservation

            # list 내부 dict가 바로 예약 객체인 경우 보완
            if isinstance(candidate, list):
                for item in candidate:
                    if isinstance(item, dict) and self._has_reservation_identity(item):
                        return item

        return None

    def fetch_data(self, room_id):
        url = URLS.get(room_id)
        if not url:
            return None

        data = self._request_json(url)
        if isinstance(data, dict):
            return data.get('list', [])
        return None

    def _find_reservation_item(self, value):
        if isinstance(value, dict):
            keys = {k.lower() for k in value.keys()}
            seat_hints = {
                'seatno', 'seatname', 'seatcode', 'code',
                'roomname', 'roomcode', 'remainingtime',
                'extensioncount', 'availableextensioncount'
            }
            reservation_hints = {
                'id', 'seat', 'room', 'seatcharge', 'seatid',
                'chargestartdatetime', 'chargeenddatetime', 'starttime', 'endtime'
            }

            if (keys & seat_hints and keys & reservation_hints) or (keys & seat_hints and 'id' in keys):
                return value

            for nested in value.values():
                found = self._find_reservation_item(nested)
                if found:
                    return found

        if isinstance(value, list):
            for item in value:
                found = self._find_reservation_item(item)
                if found:
                    return found

        return None

    def _extract_recursive_value(self, value, key_candidates):
        if value is None:
            return None

        key_candidates = {k.lower() for k in key_candidates}

        def _walk(node):
            if isinstance(node, dict):
                for key, item in node.items():
                    key_lower = str(key).lower()
                    if key_lower in key_candidates and item not in [None, "", []]:
                        return item
                for item in node.values():
                    found = _walk(item)
                    if found not in [None, "", []]:
                        return found
            elif isinstance(node, list):
                for item in node:
                    found = _walk(item)
                    if found not in [None, "", []]:
                        return found
            return None

        return _walk(value)

    def _has_reservation_identity(self, reservation):
        if not isinstance(reservation, dict):
            return False

        # 에러 응답(dict) 오인 방지
        raw_code = reservation.get('code')
        raw_message = reservation.get('message') or reservation.get('msg')
        if self._is_error_like_text(raw_code) or self._is_error_like_text(raw_message):
            return False

        room_name = self._extract_recursive_value(
            reservation,
            ['roomName', 'room', 'roomCode', 'roomNo', 'roomNm']
        )
        seat_no = self._extract_recursive_value(
            reservation,
            ['seatNo', 'seatNumber', 'seatCode', 'seatName', 'code', 'seatNoOrCode']
        )
        seat_display = reservation.get("seatDisplay")
        reservation_display = reservation.get("reservationDisplay")
        remaining_display = reservation.get("remainingDisplay")

        # seat_no가 code 필드에서 왔더라도 실제 좌석값처럼 보일 때만 인정
        valid_seat_no = self._is_plausible_seat_value(seat_no)

        return any([room_name, valid_seat_no, seat_display, reservation_display, remaining_display])

    def _is_error_like_text(self, value):
        if value in [None, ""]:
            return False

        text = str(value).strip().lower()
        error_hints = [
            "error.", "badrequest", "unauthorized", "forbidden",
            "invalid", "fail", "exception", "denied"
        ]
        return any(hint in text for hint in error_hints)

    def _is_plausible_seat_value(self, value):
        if value in [None, ""]:
            return False

        text = str(value).strip()
        if self._is_error_like_text(text):
            return False

        # 좌석번호는 일반적으로 숫자 또는 짧은 영숫자 코드
        if re.match(r"^\d{1,4}$", text):
            return True
        if re.match(r"^[A-Za-z]\d{1,4}$", text):
            return True
        return False

    def fetch_my_reservation(self):
        # 1) 실제 동작 API인 seat-charges를 우선 조회
        direct_url = "https://library.konkuk.ac.kr/pyxis-api/1/api/seat-charges"
        direct_data = self._request_json_flexible(direct_url)
        direct_reservation = self._extract_reservation_from_payload(direct_data)
        if direct_reservation:
            self.cleared_after_return = False
            self.last_reservation = direct_reservation
            return direct_reservation, None

        # 초기 실행(기존 예약/스냅샷 없음)에서 예약이 없으면 추가 조회를 생략
        if not self.last_reservation and not self.initial_reservation and not self.cleared_after_return:
            return None, None

        # 2) 나머지 후보 API 조회
        for url in MY_RESERVATION_API_CANDIDATES:
            data = self._request_json_flexible(url)
            if data is None:
                continue

            reservation = self._extract_reservation_from_payload(data)
            if reservation:
                self.cleared_after_return = False
                self.last_reservation = reservation
                return reservation, None

        reservation_from_page = self.fetch_my_reservation_from_page()
        if reservation_from_page:
            self.cleared_after_return = False
            self.last_reservation = reservation_from_page
            return reservation_from_page, None

        # 반납 성공 직후에는 조회 실패 안내 대신 "예약 정보 없음"을 우선 표시
        if self.cleared_after_return:
            return None, None

        if self.last_reservation and self._has_reservation_identity(self.last_reservation):
            return self.last_reservation, "실시간 조회 실패로 마지막 예약 정보를 표시 중"

        if self.initial_reservation and self._has_reservation_identity(self.initial_reservation):
            return self.initial_reservation, "실시간 조회 실패로 초기 스냅샷을 표시 중"

        # 예약이 없는 사용자의 초기 실행 케이스를 정상 상태로 처리
        return None, None

    def _build_web_headers(self):
        return {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://library.konkuk.ac.kr/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }

    def _decode_response_text(self, response):
        # 예약 페이지는 인코딩이 EUC-KR/CP949로 올 수 있어 안전하게 디코딩한다.
        raw = response.content or b""
        if not raw:
            return response.text or ""

        encodings = []
        if response.encoding:
            encodings.append(response.encoding)
        apparent = getattr(response, "apparent_encoding", None)
        if apparent:
            encodings.append(apparent)
        encodings.extend(["utf-8", "cp949", "euc-kr"])

        tried = set()
        for enc in encodings:
            if not enc:
                continue
            low = enc.lower()
            if low in tried:
                continue
            tried.add(low)
            try:
                text = raw.decode(enc, errors="replace")
            except Exception:
                continue

            # 예약 페이지 키워드가 보이면 해당 디코딩을 채택
            if any(keyword in text for keyword in ["예약", "열람실", "잔여시간", "연장가능시간", "로그인"]):
                return text

        # 최후 fallback
        return raw.decode("utf-8", errors="replace")

    def _looks_like_login_page(self, text):
        if not text:
            return False

        return (
            "로그인" in text
            and "예약일시" not in text
            and "잔여시간" not in text
            and "연장가능시간" not in text
        )

    def fetch_my_reservation_from_page(self):
        # 1) 쿠키 세션 기반 조회 2) 토큰 헤더 기반 조회 순서로 시도
        fetch_attempts = [
            (
                self.web_session.get,
                self._build_web_headers()
            ),
            (
                self.web_session.get,
                self._build_headers(
                    referer=MY_RESERVATION_PAGE_URL,
                    accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
                )
            )
        ]

        for request_func, headers in fetch_attempts:
            try:
                response = request_func(
                    MY_RESERVATION_PAGE_URL,
                    headers=headers,
                    timeout=7
                )
                if response.status_code != 200:
                    continue

                decoded_text = self._decode_response_text(response)
                text = re.sub(r"<[^>]+>", " ", decoded_text)
                text = re.sub(r"\s+", " ", text).strip()

                if self._looks_like_login_page(text):
                    continue

                parsed = _parse_my_reservation_from_text(decoded_text)
                if parsed:
                    return parsed
            except Exception:
                continue

        return None

    def _get_value(self, reservation, *keys):
        return self._extract_recursive_value(reservation, keys)

    def _normalize_room_display(self, room_value):
        if isinstance(room_value, dict):
            for key in ["name", "roomName", "nm", "label", "code", "id"]:
                value = room_value.get(key)
                if value not in [None, "", []]:
                    return str(value)
            return None
        return room_value

    def _normalize_seat_display(self, seat_value):
        if isinstance(seat_value, dict):
            for key in ["seatNo", "seatNumber", "seatCode", "code", "name", "id"]:
                value = seat_value.get(key)
                if value not in [None, "", []]:
                    return str(value)
            return None
        return seat_value

    def _extract_assignment_type_display(self, reservation):
        # 임시배정/배정 문구를 다양한 응답 키에서 추출
        assignment_value = self._get_value(
            reservation,
            'assignmentTypeDisplay',
            'assignmentType', 'assignmentTypeName',
            'assignType', 'assignTypeName',
            'seatAssignType', 'seatAssignTypeName',
            'chargeType', 'chargeTypeName',
            'chargeTypeCode', 'seatChargeTypeCode', 'allocationTypeCode',
            'statusName', 'seatChargeStatusName', 'seatChargeStateName',
            'allocationType', 'allocationTypeName'
        )

        if isinstance(assignment_value, dict):
            for key in ["name", "label", "text", "code", "value"]:
                value = assignment_value.get(key)
                if value not in [None, "", []]:
                    assignment_value = value
                    break

        text = str(assignment_value).strip() if assignment_value not in [None, "", []] else None

        # boolean/YN 기반 필드 보정
        temporary_flag = self._get_value(
            reservation,
            'isTemporary', 'temporary', 'tempYn', 'temporaryYn',
            'tempAssignYn', 'temporaryAssignYn',
            'isTempAssignment', 'isTemporaryAssignment'
        )
        if temporary_flag not in [None, "", []]:
            flag_text = str(temporary_flag).strip().lower()
            if flag_text in ["y", "yes", "true", "1"]:
                return "임시배정"
            if flag_text in ["n", "no", "false", "0"] and not text:
                return "배정"

        if not text:
            # 키 이름이 예상과 달라도 leaf 문자열 전체를 훑어서 임시배정 여부를 추론
            scanned_texts = []

            def _collect_leaf_texts(node):
                if isinstance(node, dict):
                    for value in node.values():
                        _collect_leaf_texts(value)
                    return
                if isinstance(node, list):
                    for item in node:
                        _collect_leaf_texts(item)
                    return

                if isinstance(node, (str, int, float, bool)):
                    scanned_texts.append(str(node).strip())

            _collect_leaf_texts(reservation)
            combined = " ".join([item for item in scanned_texts if item]).lower()

            if any(keyword in combined for keyword in ["임시", "temporary", "temp", "provisional"]):
                return "임시배정"

            # 예약 객체가 존재하면 기본값은 일반 배정으로 표시
            return "배정"

        normalized_upper = text.upper()
        if normalized_upper in ["TEMP", "TMP", "TEMPORARY", "TEMP_ASSIGN", "TEMPORARY_ASSIGNMENT"]:
            return "임시배정"
        if normalized_upper in ["NORMAL", "GENERAL", "REGULAR", "BASIC"]:
            return "배정"

        # 사람이 읽기 쉬운 표기로 정규화
        if "임시" in text:
            return "임시배정"
        if "배정" in text:
            return "배정"

        return text

    def _format_my_reservation(self, reservation, fallback_message=None):
        def _parse_datetime_for_remaining(value):
            if value in [None, "", []]:
                return None

            text = str(value).strip()
            formats = [
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%Y.%m.%d %H:%M:%S",
                "%Y.%m.%d %H:%M"
            ]

            for fmt in formats:
                try:
                    return datetime.datetime.strptime(text, fmt)
                except Exception:
                    continue

            # ISO 포맷도 허용
            try:
                return datetime.datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
            except Exception:
                return None

        def _normalize_remaining_minutes(value):
            if value in [None, "", []]:
                return None
            try:
                return int(float(str(value).strip()))
            except Exception:
                return None

        def _join_lines(lines):
            # 실시간 조회 실패 시, 마지막 스냅샷 표시 중임을 명확히 노출
            if fallback_message:
                lines.append(f"({fallback_message})")
            return "\n".join(lines)

        def _stringify_if_present(value):
            if value in [None, "", []]:
                return None
            return str(value)

        if not reservation:
            return fallback_message or "예약 정보 없음"

        room_name = self._get_value(
            reservation,
            'roomName', 'room', 'roomCode', 'roomNo', 'roomNm', 'locNm', 'locationName'
        )
        seat_no = self._get_value(
            reservation,
            'seatNo', 'seatNumber', 'seatCode', 'code', 'seatName', 'seatNm', 'number'
        )

        room_name = self._normalize_room_display(room_name)
        seat_no = self._normalize_seat_display(seat_no)

        # 에러 텍스트가 좌석번호처럼 출력되는 현상 방지
        if self._is_error_like_text(seat_no):
            seat_no = None

        display_seat = reservation.get("seatDisplay")
        if self._is_error_like_text(display_seat):
            display_seat = None

        if (not display_seat) and (room_name or seat_no):
            if room_name and seat_no:
                display_seat = f"{room_name} {seat_no}번"
            elif room_name:
                display_seat = str(room_name)
            else:
                display_seat = f"{seat_no}번"

        start_time = self._get_value(
            reservation,
            'startTime', 'startDateTime', 'beginDateTime', 'startDate',
            'chargeStartDateTime', 'chargeStartTime', 'seatChargeStartDateTime', 'seatChargeStartTime',
            'useStartDateTime', 'useStartTime', 'fromDateTime'
        )
        end_time = self._get_value(
            reservation,
            'endTime', 'endDateTime', 'expireDateTime', 'endDate',
            'chargeEndDateTime', 'chargeEndTime', 'seatChargeEndDateTime', 'seatChargeEndTime',
            'useEndDateTime', 'useEndTime', 'toDateTime'
        )
        remaining_time = self._get_value(
            reservation,
            'remainingTime', 'leftTime', 'remainTime', 'leftMinutes'
        )
        normalized_remaining_minutes = _normalize_remaining_minutes(remaining_time)

        # 일부 응답에서 remainingTime이 0으로 내려와도 종료시각이 미래면 남은 분을 보정 계산
        parsed_end_time = _parse_datetime_for_remaining(end_time)
        if (normalized_remaining_minutes is None or normalized_remaining_minutes <= 0) and parsed_end_time is not None:
            diff_seconds = int((parsed_end_time - datetime.datetime.now()).total_seconds())
            if diff_seconds > 0:
                normalized_remaining_minutes = (diff_seconds + 59) // 60

        extendable_time = self._get_value(
            reservation,
            'extendableTime', 'extendableAt', 'extensionAvailableTime', 'extensionAvailableAt'
        )
        extension = self._get_value(
            reservation,
            'extensionCount', 'usedExtensionCount', 'extendedCount'
        )
        extension_max = self._get_value(
            reservation,
            'maxExtensionCount', 'availableExtensionCount', 'extensionLimitCount'
        )

        reservation_display = _stringify_if_present(reservation.get("reservationDisplay"))
        remaining_display = _stringify_if_present(reservation.get("remainingDisplay"))
        extendable_display = _stringify_if_present(reservation.get("extendableDisplay"))
        extension_display = _stringify_if_present(reservation.get("extensionDisplay"))
        assignment_type_display = _stringify_if_present(self._extract_assignment_type_display(reservation)) or "배정"

        if display_seat:
            lines = [f"좌석: {display_seat}"]

            if assignment_type_display:
                lines.append(f"배정구분: {assignment_type_display}")

            if reservation_display:
                lines.append(f"예약시간: {reservation_display}")
            elif start_time and end_time:
                lines.append(f"예약시간: {start_time} ~ {end_time}")
            elif start_time:
                lines.append(f"시작시간: {start_time}")
            elif end_time:
                lines.append(f"종료시간: {end_time}")

            if remaining_display:
                lines.append(f"잔여시간: {remaining_display}")
            elif normalized_remaining_minutes is not None:
                lines.append(f"잔여시간: {normalized_remaining_minutes}분")

            if extendable_display:
                lines.append(f"연장가능시간: {extendable_display}")
            elif extendable_time:
                lines.append(f"연장가능시간: {extendable_time}")

            if extension_display:
                lines.append(f"연장: {extension_display}")
            elif extension is not None and extension_max is not None:
                lines.append(f"연장: {extension} / {extension_max}")

            return _join_lines(lines)

        if reservation_display or remaining_display:
            lines = ["좌석: 좌석 정보 확인 중"]
            if assignment_type_display:
                lines.append(f"배정구분: {assignment_type_display}")
            if reservation_display:
                lines.append(f"예약시간: {reservation_display}")
            if remaining_display:
                lines.append(f"잔여시간: {remaining_display}")
            if extendable_display:
                lines.append(f"연장가능시간: {extendable_display}")
            if extension_display:
                lines.append(f"연장: {extension_display}")
            return _join_lines(lines)

        room_name = self._get_value(reservation, 'roomName', 'room')
        seat_no = self._get_value(reservation, 'seatNo', 'seatNumber', 'seatCode', 'code')

        room_name = self._normalize_room_display(room_name)
        seat_no = self._normalize_seat_display(seat_no)

        seat_text = "좌석 정보 없음"
        if room_name and seat_no:
            seat_text = f"{room_name} {seat_no}번"
        elif room_name:
            seat_text = str(room_name)
        elif seat_no:
            seat_text = f"{seat_no}번"

        lines = [f"좌석: {seat_text}"]

        if assignment_type_display:
            lines.append(f"배정구분: {assignment_type_display}")

        if start_time and end_time:
            lines.append(f"예약시간: {start_time} ~ {end_time}")
        elif start_time:
            lines.append(f"시작시간: {start_time}")
        elif end_time:
            lines.append(f"종료시간: {end_time}")

        if normalized_remaining_minutes is not None:
            lines.append(f"잔여시간: {normalized_remaining_minutes}분")

        if extendable_time:
            lines.append(f"연장가능시간: {extendable_time}")

        if extension is not None and extension_max is not None:
            lines.append(f"연장: {extension} / {extension_max}")

        return _join_lines(lines)

    def _update_logic(self):
        self.root.after(0, lambda: self.status_label.config(text="🔄 데이터 갱신 중...", foreground="orange"))
        
        current_status = {}
        my_reservation = None
        reservation_msg = None
        try:
            for room_id in [102, 101]:
                seats_data = self.fetch_data(room_id)
                if seats_data:
                    for seat in seats_data:
                        code = str(seat.get('code'))
                        if (room_id, code) in self.seat_status_labels:
                            current_status[(room_id, code)] = seat

            # 최신 좌석 상태 저장
            self.latest_status_map = dict(current_status)

            # 자동 예약 대기 처리 (좌석 점유 상태 기반)
            self.process_auto_wait(current_status)

            my_reservation, reservation_msg = self.fetch_my_reservation()
        except Exception:
            current_status = None
            reservation_msg = "예약 현황을 가져오지 못했습니다."

        self.root.after(0, lambda: self.apply_updates(current_status, my_reservation, reservation_msg))

    def update_release_button_state(self, reservation):
        # 버튼 텍스트 정책
        # - 예약 없음: 비활성화 + "반납"
        # - TEMP_CHARGE: 활성화 + "예약 취소"
        # - 그 외 예약 상태: 활성화 + "반납"
        reservation_id = self.get_current_reservation_id(reservation)
        state_code = self.get_current_reservation_state_code(reservation)

        def _hide_repeat_button():
            try:
                if self.repeat_button.winfo_manager():
                    self.repeat_button.pack_forget()
            except Exception:
                pass

        def _hide_regrab_button():
            try:
                if self.regrab_button.winfo_manager():
                    self.regrab_button.pack_forget()
            except Exception:
                pass

        def _show_repeat_button():
            try:
                if not self.repeat_button.winfo_manager():
                    self.repeat_button.pack(side=tk.RIGHT, padx=(0, 6))
            except Exception:
                pass

        def _show_regrab_button():
            try:
                if not self.regrab_button.winfo_manager():
                    self.regrab_button.pack(side=tk.RIGHT, padx=(0, 6))
            except Exception:
                pass

        def _hide_repeat_countdown_label():
            try:
                if self.repeat_countdown_label.winfo_manager():
                    self.repeat_countdown_label.pack_forget()
            except Exception:
                pass

        def _show_repeat_countdown_label():
            try:
                if not self.repeat_countdown_label.winfo_manager():
                    self.repeat_countdown_label.pack(side=tk.RIGHT, padx=(0, 10))
            except Exception:
                pass

        if not reservation_id:
            self.release_button.config(text="반납", state=tk.DISABLED)
            self._set_temp_repeat_enabled(False)
            _hide_repeat_button()
            _hide_regrab_button()
            _hide_repeat_countdown_label()
            return

        if state_code == "TEMP_CHARGE":
            self.release_button.config(text="예약 취소", state=tk.NORMAL)
            _show_repeat_button()
            _show_regrab_button()
            _show_repeat_countdown_label()
            repeat_text = "반복 취소" if self._is_temp_repeat_enabled() else "반복"
            self.repeat_button.config(text=repeat_text, state=tk.NORMAL)
            self.regrab_button.config(state=tk.NORMAL)
            self.update_repeat_countdown_label(reservation)
            return

        self._set_temp_repeat_enabled(False)
        _hide_repeat_button()
        _hide_regrab_button()
        _hide_repeat_countdown_label()
        self.release_button.config(text="반납", state=tk.NORMAL)

    def apply_updates(self, current_status, my_reservation, reservation_msg):
        now = datetime.datetime.now()
        now_str = now.strftime("%H:%M:%S")

        self.current_reservation = my_reservation
        self.current_reservation_item = self._extract_real_reservation_item(my_reservation)

        self.my_reservation_label.config(
            text=self._format_my_reservation(my_reservation, reservation_msg),
            style="Seat.TLabel"
        )
        self._auto_enable_temp_repeat_for_current_reservation(my_reservation)
        self.update_release_button_state(my_reservation)
        
        if current_status is None:
            self.update_button_states()
            self.status_label.config(text=f"⚠ 갱신 실패 ({now_str}) - 토큰 만료됨", foreground="red")
            return

        for key, label_widget in self.seat_status_labels.items():
            seat_data = current_status.get(key)
            is_waiting = self._is_wait_enabled(key)
            
            if seat_data and seat_data.get('isOccupied'):
                minutes_left = seat_data.get('remainingTime')
                if minutes_left is not None:
                    parsed_minutes = None
                    try:
                        parsed_minutes = int(float(minutes_left))
                    except Exception:
                        parsed_minutes = None

                    if parsed_minutes is None:
                        display_text = "사용 중 (시간 정보 없음)"
                        if is_waiting:
                            display_text += " [예약 대기중]"
                        label_widget.config(text=display_text, style="Seat.TLabel")
                        continue

                    cached = self.seat_end_time_cache.get(key)
                    if cached and cached.get("remaining_minutes") == parsed_minutes:
                        end_time = datetime.datetime.fromtimestamp(cached.get("end_time_ts", time.time()))
                    else:
                        end_time = now + datetime.timedelta(minutes=parsed_minutes)
                        self.seat_end_time_cache[key] = {
                            "remaining_minutes": parsed_minutes,
                            "end_time_ts": end_time.timestamp()
                        }

                    ampm = "오전" if end_time.hour < 12 else "오후"
                    hour_12 = end_time.hour if end_time.hour <= 12 else end_time.hour - 12
                    if hour_12 == 0: hour_12 = 12
                    
                    end_time_str = f"{ampm} {hour_12}시 {end_time.strftime('%M')}분"
                    h, m = divmod(parsed_minutes, 60)
                    
                    display_text = f"{h}시간 {m}분 남음  ({end_time_str} 종료)"
                    if is_waiting:
                        display_text += " [예약 대기중]"
                    label_widget.config(text=display_text, style="Time.TLabel")
                else:
                    self.seat_end_time_cache.pop(key, None)
                    display_text = "사용 중 (시간 정보 없음)"
                    if is_waiting:
                        display_text += " [예약 대기중]"
                    label_widget.config(text=display_text, style="Seat.TLabel")
            elif seat_data and not seat_data.get('isOccupied'):
                self.seat_end_time_cache.pop(key, None)
                display_text = "⚪ 빈 좌석 (지금 사용 가능)"
                if is_waiting:
                    display_text += " [자동 시도중]"
                label_widget.config(text=display_text, style="Empty.TLabel")
            else:
                self.seat_end_time_cache.pop(key, None)
                label_widget.config(text="정보 없음", style="Empty.TLabel")

        self.update_button_states()
        self.next_refresh_seconds = self._compute_refresh_interval_seconds(current_status)

        if self.next_refresh_seconds in [ZERO_MINUTE_REFRESH_SECONDS, ONE_MINUTE_REFRESH_SECONDS]:
            self.scheduled_refresh_seconds = self._apply_refresh_jitter(self.next_refresh_seconds)
        else:
            self.scheduled_refresh_seconds = self._compute_half_minute_aligned_delay_seconds()

        self.status_label.config(
            text=f"✅ 최근 업데이트: {now_str} (다음 갱신 {self.scheduled_refresh_seconds}초)",
            foreground="green"
        )
        self.schedule_next_update()

    def schedule_next_update(self):
        delay_seconds = self.scheduled_refresh_seconds if self.scheduled_refresh_seconds else self._apply_refresh_jitter(self.next_refresh_seconds)
        self.next_update_at_ts = time.time() + delay_seconds
        self.timer_id = self.root.after(delay_seconds * 1000, self.update_gui)

if __name__ == "__main__":
    print("--- 자동 로그인 시스템 시작 ---")
    user_id, user_pw = get_credentials_from_env()
    token, initial_reservation, web_cookies = get_token_automatically(user_id, user_pw)
    
    if token:
        print(f"GUI 모니터를 시작합니다.")
        root = tk.Tk()
        app = SeatMonitorApp(root, token, user_id, user_pw, initial_reservation, web_cookies)
        root.mainloop()
    else:
        print("토큰을 가져오지 못해 프로그램을 종료합니다.")
        input("엔터 키를 누르면 종료됩니다...")
