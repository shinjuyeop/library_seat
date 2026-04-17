# library_seat

건국대학교 도서관 좌석 모니터링/예약 보조 GUI 도구입니다.  
좌석 상태를 주기적으로 조회하고, 예약 대기/자동 예약/임시배정 반복 기능을 제공합니다.

## 주요 기능

- 관심 좌석(`WATCH_LIST`) 상태 모니터링
- 좌석별 `예약 대기` 등록/해제
- 여러 좌석 대기 중 하나가 예약 성공하면 나머지 대기 자동 해제
- 임시배정(`TEMP_CHARGE`) 상태에서 반복(취소 후 재예약) 기능
- 임시배정 상태 진입 시 반복 기능 자동 시작
- 수동 예약, 반납(또는 임시예약 취소), 다시 잡기
- 환경 변수 기반 자동 로그인(선택)

## 실행 환경

- Python 3.10+
- Google Chrome
- Windows PowerShell 기준으로 테스트

## 설치

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install requests selenium webdriver-manager pyinstaller
```

## 환경 변수 (선택)

자동 로그인을 사용하려면 아래 값을 설정합니다.

```powershell
$env:KONKUK_LIBRARY_ID="학번/아이디"
$env:KONKUK_LIBRARY_PW="비밀번호"
```

영구 저장이 필요하면 `setx` 사용:

```powershell
setx KONKUK_LIBRARY_ID "학번/아이디"
setx KONKUK_LIBRARY_PW "비밀번호"
```

## 실행

```powershell
python library.py
```

실행 흐름:

1. Chrome을 열어 로그인/토큰을 확보
2. 내 예약 페이지를 1회 확인
3. GUI 모니터 실행

## 좌석 목록 수정

감시 좌석은 `library.py`의 `WATCH_LIST`에서 수정할 수 있습니다.

## EXE 빌드 (선택)

`library.spec` 기준으로 빌드:

```powershell
pyinstaller library.spec
```

결과물:

- `dist/library.exe`

## 트러블슈팅

- `토큰을 찾지 못했습니다`:
  로그인 완료 후 네트워크 요청이 발생했는지 확인하고 다시 실행
- 브라우저 자동 제어 실패:
  Chrome 업데이트 후 재시도
- 예약 실패/반납 실패:
  도서관 서버 상태 또는 인증 만료 가능성 있음. 재로그인 후 재시도

## 주의사항

- 비공식 개인 도구입니다.
- 학교/서비스 이용 정책을 준수해서 사용하세요.
- 계정 정보는 코드에 하드코딩하지 말고 환경 변수로 관리하세요.
