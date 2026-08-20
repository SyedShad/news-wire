import type { Metadata } from "next";
import NotificationCard from "./notification-card.tsx";

export const metadata: Metadata = {
  title: "News notification · Open Source AI News Wire",
  description: "Review a relevant News Wire item notification.",
  robots: { index: false, follow: false, nocache: true },
};

export default async function NotificationPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <NotificationCard eventId={id} />;
}
