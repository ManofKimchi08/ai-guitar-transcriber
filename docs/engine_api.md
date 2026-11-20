# 로컬 엔진 API

웹페이지는 사용자 PC에서 켜 둔 엔진(`engine.py`, `run_engine.bat`)에 작업을 보냅니다. 변환은 그 PC의 GPU로 합니다. 엔진은 `http://127.0.0.1:51823`에서만 요청을 받고 결과는 데스크톱 GUI와 같은 `output/` 폴더에 저장합니다.

## 실행

```
run_engine.bat                 :: 설치가 끝난 PC에서 (start.bat으로 먼저 설치)
python engine.py --no-browser  :: 웹페이지를 자동으로 열지 않음
python engine.py --port 52000  :: 다른 포트 사용
python engine.py --reset-token :: 연결된 브라우저를 모두 해제
python engine.py --allow-origin https://example.com  :: 다른 사이트 허용
```

엔진을 켜면 창에 **연결 코드**(6자리)가 나오고 웹페이지가 `…/#pair=<코드>&port=<포트>` 주소로 열립니다. 페이지는 그 코드로 `/api/pair`를 불러 토큰을 받고 브라우저에 저장합니다. 브라우저마다 한 번만 하면 됩니다.

- 코드는 한 번만 쓸 수 있습니다. 쓰고 나거나 5번 틀리면 새 코드가 창에 나옵니다.
- 토큰은 `engine_data/engine.json`에 저장되므로 엔진을 다시 켜도 그대로입니다.

## 보안 규칙

| 검사 | 막는 것 |
|---|---|
| 127.0.0.1에서만 대기 | 다른 PC에서의 접속 |
| `Host`가 `127.0.0.1:<포트>` 또는 `localhost:<포트>`가 아니면 403 | DNS 리바인딩 |
| `Origin`이 허용 목록(`https://manofkimchi08.github.io`, 이 PC의 `http://localhost:*`)에 없으면 403 | 다른 사이트에서의 호출 |
| `/api/status`, `/api/pair` 말고는 `Authorization: Bearer <토큰>`이 없으면 401 | 연결하지 않은 페이지 |

CORS 사전 요청(OPTIONS)에는 허용된 사이트에만 답합니다. 크롬이 보내는 `Access-Control-Request-Private-Network`에도 응답합니다.

## 엔드포인트

모든 응답은 JSON(UTF-8)입니다. 오류는 `{"error": "한국어 설명"}` 형태입니다.

| 메서드 · 경로 | 토큰 | 설명 |
|---|---|---|
| `GET /api/status` | 불필요 | 엔진 확인용. `app`, `version`, `paired`, `gpu`(`{name, vram_gb}` 또는 `null`), `gpu_checked`, `busy`, `job_id`, `parts`, `default_parts`, `vram_profiles`, `styles` |
| `POST /api/pair` `{"code": "123456"}` | 불필요 | `{"token"}`. 코드가 틀리면 403 |
| `POST /api/uploads` | 필요 | 본문은 음원 파일 그대로, 헤더 `X-File-Name`에 파일 이름(URL 인코딩). 최대 1GB. `{"upload_id", "name", "size"}` (201). 파일 이름이 곡 제목이 됩니다. 3일 지난 업로드는 엔진을 켤 때 지웁니다 |
| `POST /api/jobs` | 필요 | 변환 시작. 202, 작업 상태를 돌려줌. 다른 작업이 진행 중이면 409 |
| `GET /api/jobs/current?log_from=N` | 필요 | 마지막 작업 상태, 없으면 `null`. 페이지를 새로 고친 뒤 이어 보기용 |
| `GET /api/jobs/<id>?log_from=N` | 필요 | 작업 상태. `log`는 N번째 줄부터 |
| `POST /api/jobs/<id>/cancel` | 필요 | 취소 요청. 진행 중인 Demucs도 멈춥니다 |
| `GET /api/songs` | 필요 | 변환된 곡 목록(최신순): `name`, `tags`, `mtime`, `score`, `pdf`, `files`(`[{path, size}]`) |
| `GET /api/songs/<곡>/files/<경로>` | 필요 | 곡 폴더 안 `scores/`, `midi/`, `stems/`의 파일 |

### 작업 시작 본문

```json
{
  "upload_id": "0a1b2c3d4e5f",
  "parts": ["drums", "bass", "rhythm", "lead", "vocals"],
  "style": "tab",
  "vram": null,
  "lyrics": true,
  "lyrics_language": "ko"
}
```

- 파일 대신 `"url": "https://youtu.be/..."`을 넣을 수 있습니다.
- `parts`: `drums`, `bass`, `rhythm`, `lead`, `vocals` 중에서 고릅니다. 기본값은 보컬을 뺀 넷입니다.
- `style`: `tab`, `standard`, `both`.
- `vram`: `8gb`, `4gb`, 또는 `null`(자동).
- `lyrics_language`: `ko`, `en`, `ja` 같은 언어 코드. `null`이면 자동으로 알아냅니다.

### 작업 상태

```json
{
  "id": "…", "state": "running", "input": "My Song.mp3", "options": {…},
  "stages": ["separate", "split"], "stage": "split",
  "status": "기타 분리 중...", "fraction": 0.35,
  "log": ["…"], "log_next": 42,
  "result": null, "error": null, "elapsed": 81.2
}
```

- `state`: `running`에서 시작해 `done`, `error`, `cancelled` 중 하나로 끝납니다.
- `stages`: 시작된 단계 순서. 단계 키는 `download`, `separate`, `split`, `transcribe`, `lyrics`, `score`이고, 하지 않는 단계는 나오지 않습니다.
- `log_next`: 다음 요청에 `log_from`으로 넘길 값입니다.
- `result` (`done`일 때): `song`(곡 폴더 이름), `song_title`, `score`, `pdf`, `lyrics`(곡 폴더 기준 경로 또는 `null`), `elapsed`, `bpm`.
