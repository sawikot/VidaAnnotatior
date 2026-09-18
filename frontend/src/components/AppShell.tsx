import { Outlet } from "react-router-dom";
import { IconRail } from "./IconRail";
import { TopHeader } from "./TopHeader";
import { ToastHost } from "./ToastHost";

export function AppShell() {
  return (
    <div className="min-h-screen bg-surface">
      <IconRail />
      <TopHeader />
      <div className="pl-14">
        <main className="w-full pt-14 bg-surface min-h-screen">
          <Outlet />
        </main>
      </div>
      <ToastHost />
    </div>
  );
}
