import { useEffect, useState } from "react";
import QRCode from "qrcode";

/** Renders text (a UPI link, a table ordering URL) as a QR image. */
export function QrImage({ text, size = 220, label }: { text: string; size?: number; label?: string }) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    QRCode.toDataURL(text, { width: size, margin: 1, errorCorrectionLevel: "M" })
      .then((url: string) => live && setSrc(url))
      .catch(() => live && setSrc(null));
    return () => { live = false; };
  }, [text, size]);
  return src ? <img src={src} width={size} height={size} alt={label || "QR code"} className="qr-img" /> : <div className="qr-img" style={{ width: size, height: size }} />;
}
