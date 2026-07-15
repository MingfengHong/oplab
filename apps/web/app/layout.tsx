import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Oplab · 一人课题组",
  description: "Evidence-first research operating system",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
