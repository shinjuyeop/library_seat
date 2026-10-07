# Vercel + Supabase 배포

## 현재 운영 배포

- 운영 주소: https://library-seat-dusky.vercel.app
- Vercel 프로젝트: `juyeop-shins-projects/library-seat`, GitHub `main` 자동 배포
- Supabase 프로젝트: `library-seat` (`izdxeebrvneysoydsugg`, 서울)
- 2026-10-07: 운영 웹앱 로그인, 상태 조회, 인증 없는 API 접근 차단을 확인했습니다.
- Supabase Cron의 `30 seconds` 작업과 Vercel의 HTTP 200 응답, 저장된 실행 시각을 확인했습니다.
- 도서관 계정 연결과 실제 좌석 예약은 아직 검증하지 않았습니다. 아래 4단계로 연결해야 합니다.

이 PC의 접속 비밀번호는 Git에서 제외된 `data/webapp-access.txt`에 있습니다.
이 파일과 `data/deployment-secrets.json`은 현재 Windows 사용자만 접근하도록 설정했습니다.

## 구조

```text
아이폰 Safari / 홈 화면 웹앱
             │ 시작·중지·조회
             ▼
Vercel: Flask 웹/API ──────── 도서관 API
             │                 좌석 조회·임시예약
             ▼
Supabase: 상태 + 암호화된 인증 정보
             ▲
Supabase Cron ── 30초 간격으로 Vercel /api/cron 호출
```

브라우저의 타이머나 Vercel의 백그라운드 스레드에 의존하지 않습니다.
Cron 실행마다 저장된 대기를 복원해 한 번 확인하고 종료합니다.
실행 간격은 보장된 예약 시각이 아니며, 이전 작업이 진행 중이면 새 작업은 건너뜁니다.
Vercel Hobby Cron은 하루 단위 제한이 있어 이 프로젝트는 **Supabase Cron**을 사용합니다.

## 1. Supabase 준비

1. 이 프로젝트 전용 Supabase 프로젝트를 만듭니다. 기존 프로젝트를 쓸 경우 `library_*` 테이블/함수 이름이 겹치지 않는지 확인합니다.
2. SQL Editor에서 [마이그레이션](supabase/migrations/202610070001_runtime.sql)을 실행합니다.
3. Database > Extensions에서 `pg_cron`, `pg_net`을 활성화합니다.
4. 프로젝트 URL과 서버용 `service_role` 키를 Vercel 환경 변수에 넣습니다. 브라우저용 publishable/anon 키를 쓰면 안 됩니다.

두 테이블에는 RLS를 켜고 `anon`, `authenticated` 접근을 차단했습니다. 서비스 역할 키는 Vercel 서버에서만 사용합니다.
도서관 로그인 토큰과 쿠키는 Fernet으로 암호화하며, 복호화 키는 Vercel 환경 변수에만 둡니다.

## 2. Vercel 환경 변수

GitHub 저장소를 Vercel에 연결하고 Framework Preset은 **Flask**, Root Directory는 저장소 루트로 설정합니다.
별도 프런트엔드 빌드는 없습니다. `app.py`, `public/`, `vercel.json`이 배포 진입점입니다.
Production 환경에 다음 값을 설정하세요. 키를 소스나 채팅에 붙여 넣지 마세요.

| 변수 | 값 |
| --- | --- |
| `SUPABASE_URL` | `https://프로젝트ID.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase 서버용 service_role 키 |
| `LIBRARY_WEB_PASSWORD` | 웹앱 개인 접속 비밀번호. 무작위 16자 이상 |
| `LIBRARY_SECRET_KEY` | 모든 Vercel 인스턴스가 공유할 세션 서명용 무작위 값 |
| `LIBRARY_ENCRYPTION_KEY` | 아래 명령으로 생성한 Fernet 키 |
| `CRON_SECRET` | Supabase Cron 호출을 인증할 별도의 무작위 32자 이상 값 |
| `LIBRARY_TRUSTED_HOSTS` | 선택. 실제 운영 호스트명. 여러 개면 쉼표 구분 |

키 생성은 로컬 터미널에서 실행하고, 출력된 값을 해당 환경 변수에만 저장합니다.

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

두 번째 명령을 각각 실행해 `LIBRARY_SECRET_KEY`, `CRON_SECRET`에 서로 다른 값을 사용하세요.
암호화 키를 바꾸면 기존 도서관 연결은 복원되지 않으므로 다시 연결해야 합니다.
클라우드는 HTTPS 전용 쿠키를 사용합니다. `LIBRARY_COOKIE_SECURE=0`을 운영 환경에 설정하지 마세요.

배포 후 `/healthz`가 응답하고 접속 비밀번호로 웹앱에 들어갈 수 있는지 확인합니다.
`/healthz`는 HTTP 앱의 준비 상태이며 Cron 또는 도서관 접속 성공을 뜻하지 않습니다.
Vercel Deployment Protection이 Cron 요청을 막으면 운영 도메인의 접근 정책을 조정해야 합니다.
웹앱 자체 비밀번호와 Cron 인증은 유지하세요.

## 3. 30초 간격 자동 실행

Supabase Dashboard의 Vault에 아래 두 Secret을 만듭니다.

- `library_app_url`: 최종 운영 주소. 예: `https://your-project.vercel.app` (끝에 `/` 없음)
- `library_cron_secret`: Vercel에 설정한 `CRON_SECRET`과 정확히 같은 값

그다음 SQL Editor에서 [schedule.sql](supabase/schedule.sql)을 실행합니다.
이 스크립트는 이름이 같은 작업만 교체하며 다른 스케줄에는 영향을 주지 않습니다.
Vercel Cron 설정은 추가하지 않습니다. 프리뷰 배포 주소에 Cron을 연결하지 마세요.

확인:

```sql
select jobid, jobname, schedule, active from cron.job where jobname = 'library-seat-poll';
select status, return_message, start_time from cron.job_run_details order by start_time desc limit 10;
select status_code, timed_out, error_msg, created from net._http_response order by created desc limit 10;
```

Cron SQL이 성공해도 HTTP 요청은 실패할 수 있으므로 응답 코드와 웹앱의 스케줄러 안내를 함께 확인합니다.
작업 중복은 데이터베이스의 150초 임대 잠금으로 막습니다. Vercel의 최대 실행 시간은 120초로 설정되어 있습니다.
**최대 실행 시간을 임대 시간 이상으로 늘리지 마세요.** 예약을 전송하기 전에 대기를 중지 상태로 저장해,
요청 도중 프로세스가 종료되어도 같은 요청을 자동 재전송하지 않습니다.

자동 실행 중지:

```sql
select cron.unschedule(jobid) from cron.job where jobname = 'library-seat-poll';
```

## 4. 도서관 로그인 연결

기존 프로그램은 Chrome을 열어 도서관 로그인 토큰을 얻습니다. Vercel에서는 Chrome을 실행하지 않습니다.
Chrome이 설치된 PC에서 다음 명령을 실행합니다.

```powershell
python connect_cloud.py https://your-project.vercel.app
```

1. 명령에 **본인 소유의 최종 배포 주소**를 넣습니다.
2. 터미널에서 웹앱 접속 비밀번호를 입력합니다.
3. 열린 Chrome에서 공식 도서관 홈페이지에 로그인합니다.
4. 연결 도구는 로그인 토큰·쿠키를 해당 웹앱으로 HTTPS 전송합니다. 출력하거나 파일에 저장하지 않습니다.
5. 서버에서 내 예약 조회가 성공해야 연결이 저장됩니다.

이후 휴대폰에서 좌석을 선택하고 **자동 예약 시작**을 누르면 됩니다. PC를 꺼도 됩니다.
도서관 인증이 만료되면 서버는 예약을 보류하며, 위 명령으로 다시 연결해야 합니다.
만료 전에 등록한 대기는 재연결 후 다시 실행될 수 있습니다. 원하지 않으면 웹앱에서 먼저 중지하세요.
도서관 서버가 클라우드 IP 접근을 차단하면 이 구성에서도 요청이 실패할 수 있습니다. 실제 배포에서 확인해야 합니다.

## 5. 아이폰 사용

Safari에서 운영 주소를 열고 접속합니다. 공유 > 홈 화면에 추가로 앱처럼 열 수 있습니다.
대기 등록은 서버에 저장됩니다. 화면을 끄거나 웹앱을 닫아도 Cron이 실행되는 동안 대기는 유지됩니다.
푸시 알림은 포함하지 않았습니다. 다시 열면 최신 처리 결과를 조회합니다.
임시배정 성공 후에는 공식 도서관 앱으로 현장의 NFC 태그를 읽어 배정을 확정해야 합니다.
임시배정의 유효 시간은 도서관 서버 기준이며, 웹앱이 자동으로 연장하지 않습니다.

## 운영 범위와 검증 상태

- 개인 계정 하나를 위한 배포입니다. 웹앱 비밀번호를 공유하면 같은 도서관 계정을 조작할 수 있습니다.
- 30초마다 실행하면 하루 최대 2,880회, 30일 약 86,400회 호출됩니다. 대기가 없으면 도서관 조회는 줄이지만 Cron 호출은 유지됩니다.
- 무료 제공량 안에서 동작하는지는 다른 프로젝트 사용량과 현재 요금제를 포함해 확인해야 합니다. 무료·무중단 운영을 보장하지 않습니다.
- 로컬 단위 테스트·가상 좌석 UI와 실제 Vercel/Supabase 배포·Cron 연결을 검증했습니다. 실제 도서관 계정 연결·예약 검증은 별도입니다.
- 배포 후 데스크톱 GUI와 웹앱에서 같은 계정의 자동 예약을 동시에 실행하지 마세요.

공식 참고: [Vercel Flask](https://vercel.com/docs/frameworks/backend/flask), [Supabase Cron](https://supabase.com/docs/guides/cron), [Vercel Cron 제한](https://vercel.com/docs/cron-jobs/usage-and-pricing)
