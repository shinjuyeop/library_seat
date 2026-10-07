# Vercel + Supabase 배포

운영 주소: https://library-seat-dusky.vercel.app

## 사용

1. 웹에서 본인의 **도서관 아이디·비밀번호**로 로그인합니다. 별도 웹앱 비밀번호나 PC 연결 도구는 필요하지 않습니다.
2. `1인석` 또는 `전체 좌석`에서 열람실·좌석 번호를 검색합니다. 1열람실 A/B, 2열람실, 3열람실 A/B, 5열람실을 지원합니다.
3. 빈 좌석의 `바로 예약`을 누르거나, 원하는 좌석들을 선택하고 `자동 예약 시작`을 누릅니다. 최대 50개를 선택할 수 있고 선택한 순서대로 시도합니다.
4. 브라우저·PC가 꺼져 있어도 서버에서 대기합니다. 한 좌석이 예약되면 그 계정의 나머지 대기는 종료됩니다.
5. 임시배정 후 현장에서 공식 앱의 NFC 인증으로 배정을 확정합니다.

로그인은 공식 `/pyxis-api/api/login`의 명시적 성공 응답과 인증된 내 예약 조회가 모두 확인되어야 완료됩니다.
실패하면 새 계정을 등록하거나 입력한 비밀번호를 저장하지 않습니다. 오류를 표시하고 비밀번호를 다시 입력하도록 합니다.
도서관 서버 차단·통신 오류는 비밀번호 오류와 구분합니다. 추가 인증·약관 동의·비밀번호 변경이 필요한 경우 공식 사이트에서 처리해야 합니다.

`자동로그인`을 선택하면 이 기기의 로그인은 최대 30일 유지되며, 도서관 아이디·비밀번호는 서버에서 암호화해 저장합니다.
도서관 인증이 만료되면 다음 스케줄에서 재로그인합니다. 비밀번호가 거절되면 저장된 자동로그인 정보를 제거하고 대기를 중지합니다.
통신 오류는 5분 이상 간격을 두고 재시도합니다. 자동로그인하지 않으면 비밀번호를 저장하지 않으며 인증 만료 시 다시 입력해야 합니다.
`연결 해제`는 해당 계정의 저장된 로그인과 대기를 제거합니다. `이 기기 로그아웃`은 해당 브라우저의 로그인만 종료합니다.

## 여러 계정

로그인한 사람마다 공식 도서관의 이용자 ID에 대응하는 별도 상태와 작업 잠금을 사용합니다.
다른 사람의 좌석·예약·대기·로그인 정보는 조회하거나 조작할 수 없습니다.
클라이언트가 보낸 계정 식별자는 예약 API에서 사용하지 않고 서명된 서버 세션으로 계정을 결정합니다.
같은 도서관 계정을 여러 기기에서 쓰면 같은 대기 상태를 공유합니다.

Supabase `library_accounts`에는 RLS를 켜고 `anon`, `authenticated`, `PUBLIC` 접근을 차단합니다.
서버만 service_role 키로 접근합니다. 로그인 토큰·쿠키와 선택적으로 저장한 아이디·비밀번호는 Fernet으로 암호화합니다.
세션 쿠키에는 암호나 도서관 토큰을 넣지 않습니다. 비밀 키는 Git, 프런트엔드, 로그에 저장하지 않습니다.
운영자가 서버 암호화 키에 접근하면 저장된 로그인 정보를 복호화할 수 있으므로 신뢰하는 운영자의 서비스에서만 자동로그인을 사용해야 합니다.

## 배포 설정

- Vercel: `juyeop-shins-projects/library-seat`, Flask, 저장소 루트, `main` 자동 배포, 서울 리전
- Supabase: `library-seat` (`izdxeebrvneysoydsugg`, 서울)
- Python 진입점: `app.py`, 정적 파일: `public/`

Vercel Production 환경 변수:

| 변수 | 용도 |
| --- | --- |
| `SUPABASE_URL` | 프로젝트 URL |
| `SUPABASE_SERVICE_ROLE_KEY` | 서버 전용 service_role 키 |
| `LIBRARY_SECRET_KEY` | 세션 서명용 무작위 키 |
| `LIBRARY_ENCRYPTION_KEY` | Fernet 키. 계정 구분에도 사용하므로 임의 변경 금지 |
| `CRON_SECRET` | 스케줄 요청 인증용 32자 이상의 무작위 키 |
| `LIBRARY_WEB_PASSWORD` | 기존 로컬 앱 호환용. 클라우드 로그인에는 사용하지 않음 |

Supabase SQL Editor에서 마이그레이션을 순서대로 적용합니다.

1. `supabase/migrations/202610070001_runtime.sql` — 기존 테이블과 공통 로그인 요청 제한
2. `supabase/migrations/202610070002_accounts.sql` — 계정별 저장·잠금
3. `supabase/migrations/202610070003_poll_dispatch.sql` — 다음 실행 시각과 HTTP 요청 중복 방지
4. `pg_cron`과 `pg_net` 확장 활성화
5. Vault에 `library_app_url`(운영 주소)과 `library_cron_secret`(`CRON_SECRET`과 같은 값) 저장
6. 새 앱 배포 후 `supabase/schedule.sql` 실행

DB 스케줄은 1초마다 실행 시각이 도래한 계정만 찾아 인증된 `/api/cron` 요청을 보냅니다.
대기 중 선택 좌석이 **0~1분 남았거나 빈자리이면 1초**, 그 외에는 30초 주기를 목표로 합니다.
집중 확인 중에는 선택한 좌석이 있는 열람실만 갱신합니다. 다른 열람실은 마지막 조회 시각을 표시하며 새로고침으로 전체를 확인합니다.
임시배정 상태는 30초, 대기하지 않는 계정은 5분 간격으로 조회합니다. 인증 재시도는 별도 5분 지연을 유지합니다.
다음 실행 시각과 예약된 HTTP 호출의 기한을 DB에 저장해 시작 전 중복 호출도 막습니다. 네트워크 지연이나 진행 중 작업을 건너뛰므로 정확한 1초 간격을 보장하지 않습니다.
계정별 150초 잠금과 예약 전 대기 해제 저장으로 중복 요청을 막습니다. Vercel 최대 실행 시간 120초를 잠금 시간 이상으로 늘리지 마세요.
실행 시각은 지연될 수 있으며, 예약 성공은 다른 이용자와 도서관 서버 상태에 따라 달라집니다.
계정 수와 집중 확인 시간에 따라 호출량이 증가하므로 무료 제공량 내 운영은 보장하지 않습니다. 1초 확인 1분은 최대 약 60회 호출이며, 평상시 대기는 시간당 약 120회입니다.
1초 DB 스케줄 자체의 실행 기록도 쌓이므로 `cron.job_run_details`와 DB 저장 용량을 확인해야 합니다.

스케줄 확인:

```sql
select jobname, schedule, active from cron.job where jobname = 'library-seat-poll';
select status_code, timed_out, error_msg, created from net._http_response order by created desc limit 10;
```

`/healthz`는 HTTP 앱 상태만 표시합니다. 실제 로그인·좌석 예약 성공을 뜻하지 않습니다.
이전 단일 사용자용 `library_runtime`은 신규 웹앱에서 사용하지 않습니다. 기존 PC 연결 도구도 사용하지 않습니다.

## 검증

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -p test_service.py -v
node --check public/assets/app.js
```

로그인 실패 시 저장 방지, 두 계정의 세션·예약·잠금 분리, 암호화, 인증 만료 후 자동로그인, 잘못된 비밀번호 재시도 중단,
중복 예약 방지와 불명확한 예약 결과의 자동 재전송 방지를 검사합니다.
6개 열람실의 일반 좌석 예약, 없는 좌석 거절, 0~1분 경계/누락 시간/통신 오류 처리, 집중 확인의 조회 범위와 다음 실행 시각도 검사합니다.
모의 계정의 모바일 UI 검증과 실제 도서관 계정의 예약 검증을 구분합니다. 실제 예약은 로그인 후 확인해야 합니다.

공식 참고: [Vercel Flask](https://vercel.com/docs/frameworks/backend/flask), [Supabase Cron](https://supabase.com/docs/guides/cron)
