/**
 * Capacitor(iOS 앱) 연동 헬퍼.
 * 일반 브라우저에서는 window.Capacitor가 없으므로 모든 함수가 안전하게 no-op/폴백 처리됨.
 */

function isNativeApp() {
  return !!(window.Capacitor && window.Capacitor.isNativePlatform && window.Capacitor.isNativePlatform());
}

/**
 * 카메라 촬영 또는 사진 라이브러리에서 선택해 File 객체로 반환.
 * 네이티브 앱이 아니면 null을 반환(호출 측에서 기존 <input type="file">로 폴백).
 */
async function captureOrPickImage() {
  if (!isNativeApp()) return null;
  const { Camera, CameraResultType, CameraSource } = window.Capacitor.Plugins;
  const photo = await Camera.getPhoto({
    resultType: CameraResultType.Uri,
    source: CameraSource.Prompt,
    quality: 85,
  });
  const resp = await fetch(photo.webPath);
  const blob = await resp.blob();
  const ext = (photo.format || "jpeg").toLowerCase();
  return new File([blob], `photo_${Date.now()}.${ext}`, { type: blob.type || `image/${ext}` });
}

/**
 * 텍스트/HTML 파일을 기기에 저장하고 공유 시트를 띄움.
 * 네이티브 앱이 아니면 false를 반환(호출 측에서 기존 <a download> 방식으로 폴백).
 */
async function saveAndShareFile(filename, content, mimeType) {
  if (!isNativeApp()) return false;
  const { Filesystem, Directory, Share } = window.Capacitor.Plugins;
  const base64 = btoa(unescape(encodeURIComponent(content)));
  const result = await Filesystem.writeFile({
    path: filename,
    data: base64,
    directory: Directory.Cache,
    recursive: true,
  });
  await Share.share({
    title: filename,
    url: result.uri,
    dialogTitle: "파일 저장/공유",
  });
  return true;
}

/**
 * 푸시 알림 권한을 요청하고, 발급된 디바이스 토큰을 서버에 등록.
 * 네이티브 앱이 아니면 아무 동작도 하지 않음.
 */
async function initPushNotifications() {
  if (!isNativeApp()) return;
  const { PushNotifications } = window.Capacitor.Plugins;

  const perm = await PushNotifications.requestPermissions();
  if (perm.receive !== "granted") return;

  await PushNotifications.register();

  PushNotifications.addListener("registration", async (token) => {
    try {
      await fetch("/push/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: token.value, platform: "ios" }),
      });
    } catch (e) {
      console.error("푸시 토큰 등록 실패", e);
    }
  });

  PushNotifications.addListener("registrationError", (err) => {
    console.error("푸시 등록 오류", err);
  });
}
