# OpenSimul iOS 앱 (Capacitor)

기존 FastAPI 웹 앱(`app.py`, `static/`)을 그대로 두고, Capacitor로 iOS 네이티브 셸을 만들어
카메라 / 파일 저장·공유 / 푸시 알림 기능을 추가합니다. 웹 브라우저로 접속하는 기존 서비스는
변경 없이 그대로 동작합니다.

빌드와 App Store 제출은 **macOS + Xcode**가 필요합니다. 이 폴더는 그 작업을 위한
설정/스켈레톤만 포함하며, 실제 iOS 프로젝트(`ios/`)는 Mac에서 생성합니다.

## 1. 사전 준비

- Node.js 18+ 설치
- macOS + Xcode 15+ (App Store 제출용)
- 배포된 OpenSimul 서버의 **HTTPS** 도메인 (Apple ATS 정책상 HTTP는 허용되지 않습니다)

## 2. 설정

`capacitor.config.json`의 `server.url`과 `allowNavigation`을 실제 배포 도메인으로 수정하세요.

```json
{
  "appId": "kr.mo.opensimul",
  "appName": "OpenSimul",
  "webDir": "www",
  "server": {
    "url": "https://your-real-domain.com",
    "allowNavigation": ["your-real-domain.com"]
  }
}
```

> SSL 인증서가 아직 없다면, 인증서 발급 전까지는 이 앱을 빌드/제출할 수 없습니다.
> (HTTP 평문 통신은 App Store 심사에서 거부됩니다.)

## 3. iOS 프로젝트 생성 (Mac에서)

```bash
cd mobile
npm install
npx cap add ios
npx cap sync ios
npx cap open ios   # Xcode가 열립니다
```

## 4. Info.plist 권한 문구 추가

`ios/App/App/Info.plist`에 다음 키를 추가하세요(카메라/사진 사용 안내문).

```xml
<key>NSCameraUsageDescription</key>
<string>영수증을 촬영해 자동으로 등록하기 위해 카메라를 사용합니다.</string>
<key>NSPhotoLibraryUsageDescription</key>
<string>저장된 영수증 사진을 불러오기 위해 사진 보관함에 접근합니다.</string>
```

## 5. 푸시 알림(APNs) 설정

1. Apple Developer 계정에서 **APNs 키(.p8)** 발급
2. Xcode 프로젝트의 `Signing & Capabilities`에서 **Push Notifications**, **Background Modes
   → Remote notifications** 활성화
3. 앱이 첫 로그인 시 디바이스 토큰을 서버의 `POST /push/register`로 전송합니다
   (`static/native.js`의 `initPushNotifications()` 참고).
4. 서버에서 실제 알림 발송은 `db.list_push_tokens(user_code)`로 토큰을 조회한 뒤,
   APNs(또는 APNs를 대신 처리하는 Firebase Cloud Messaging 등)로 푸시를 보내는
   별도 발송 로직을 추가해야 합니다. 현재는 토큰 등록/저장까지만 구현되어 있습니다.

## 6. 동작 방식 요약

- 앱은 `server.url`에 설정한 배포 서버 페이지를 그대로 로드하는 웹뷰입니다.
- `static/native.js`가 `window.Capacitor` 존재 여부로 네이티브 환경을 감지해
  - 카메라 촬영/사진 선택 (`Camera` 플러그인)
  - 파일 저장 및 공유 시트 (`Filesystem`, `Share` 플러그인)
  - 푸시 토큰 등록 (`PushNotifications` 플러그인)
  을 제공하며, 일반 브라우저에서는 모두 기존 웹 동작(파일 입력, `<a download>`)으로
  자동 폴백됩니다.
