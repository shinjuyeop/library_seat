"""Shared room and polling configuration."""

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

