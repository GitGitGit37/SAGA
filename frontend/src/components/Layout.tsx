import { Bot, LayoutGrid, MessageSquareText, Sparkles, Upload } from "lucide-react";
import { Navigate, NavLink, Outlet, useLocation } from "react-router-dom";
import { useSession } from "@/lib/session";
import { renterText } from "@/lib/renterLanguage";
import { cn, llmLabel, ROLE_LABEL } from "@/lib/utils";

const NAV = [
  { to: "/", label: "Fleet", icon: LayoutGrid, end: true },
  { to: "/ingest", label: "Ingest data", icon: Upload, end: false },
  { to: "/ask", label: "Ask the machine", icon: MessageSquareText, end: false },
];

export function Layout() {
  const { users, user, setUserId, viewMode, setViewMode, renterLanguage, meta } = useSession();
  const location = useLocation();

  if (viewMode === "renter") {
    if (location.pathname !== "/" && !location.pathname.startsWith("/assets/")) {
      return <Navigate to="/" replace />;
    }

    return (
      <div className="min-h-screen bg-zinc-100">
        <header className="border-b-4 border-amber-400 bg-zinc-950 px-4 py-4 text-white md:px-8">
          <div className="mx-auto flex max-w-5xl items-center justify-between gap-4">
            <div>
              <div className="text-sm font-semibold">Ride Along</div>
              <div className="text-xs text-zinc-400">{renterText(renterLanguage, "app.subtitle")}</div>
            </div>
            <button
              type="button"
              onClick={() => setViewMode("staff")}
              className="rounded-md border border-zinc-700 px-3 py-2 text-sm hover:bg-zinc-800"
            >
              {renterText(renterLanguage, "app.exit")}
            </button>
          </div>
        </header>
        <main className="mx-auto max-w-5xl px-4 py-6 md:px-8 md:py-8">
          <Outlet />
        </main>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      <aside className="flex shrink-0 flex-col bg-zinc-950 text-zinc-300 md:sticky md:top-0 md:h-screen md:w-60">
        <div className="flex items-center gap-2 px-5 py-5">
          <div className="grid size-8 place-items-center rounded-md bg-amber-400 font-black text-zinc-950">RA</div>
          <div>
            <div className="text-sm font-semibold text-white">Ride Along</div>
            <div className="text-[11px] text-zinc-500">Memory for physical AI</div>
          </div>
        </div>
        <nav className="flex gap-1 overflow-x-auto px-3 md:flex-col">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm whitespace-nowrap transition-colors",
                  isActive ? "bg-zinc-800 text-white" : "hover:bg-zinc-900 hover:text-white",
                )
              }
            >
              <Icon className="size-4" />
              {label}
            </NavLink>
          ))}
        </nav>

        <div className="mt-auto space-y-3 border-t border-zinc-800 p-4">
          <div className="flex items-center gap-2 text-xs">
            {meta?.llm_enabled ? (
              <>
                <Sparkles className="size-3.5 text-amber-400" /> {llmLabel(meta.llm_provider)} connected
              </>
            ) : (
              <>
                <Bot className="size-3.5 text-zinc-500" /> Rules mode (no API key)
              </>
            )}
          </div>
          <div>
            <div className="mb-1 text-[11px] uppercase tracking-wide text-zinc-500">Acting as</div>
            <select
              className="w-full rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm text-white focus:outline-none focus:ring-2 focus:ring-amber-500"
              value={user?.id ?? "staff"}
              onChange={(e) => {
                if (e.target.value === "renter") {
                  setViewMode("renter");
                } else if (e.target.value === "staff") {
                  setViewMode("staff");
                } else {
                  setViewMode("staff");
                  setUserId(Number(e.target.value));
                }
              }}
            >
              <option value="staff" disabled>Staff view</option>
              <option value="renter">Renter · first-time user</option>
              <optgroup label="Staff members">
                {users.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.name} · {ROLE_LABEL[u.role] ?? u.role}
                  </option>
                ))}
              </optgroup>
            </select>
            {user && (
              <div className="mt-1.5 text-[11px] text-zinc-500">
                Reliability <span className="tabular text-zinc-300">{user.reliability.toFixed(2)}</span> · role weight{" "}
                <span className="tabular text-zinc-300">{meta?.scoring.feedback.role_weights[user.role] ?? "–"}</span>
              </div>
            )}
          </div>
        </div>
      </aside>
      <main className="min-w-0 flex-1 px-4 py-6 md:px-8 md:py-8">
        <div className="mx-auto max-w-6xl">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
