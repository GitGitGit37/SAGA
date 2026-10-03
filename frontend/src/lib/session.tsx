import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, type Meta, type User } from "./api";

export type RenterLanguage = "en" | "es" | "fr";

type Session = {
  users: User[];
  user: User | null;
  setUserId: (id: number) => void;
  viewMode: "staff" | "renter";
  setViewMode: (mode: "staff" | "renter") => void;
  renterLanguage: RenterLanguage;
  setRenterLanguage: (language: RenterLanguage) => void;
  refreshUsers: () => void;
  meta: Meta | null;
};

const SessionContext = createContext<Session>({
  users: [],
  user: null,
  setUserId: () => {},
  viewMode: "staff",
  setViewMode: () => {},
  renterLanguage: "en",
  setRenterLanguage: () => {},
  refreshUsers: () => {},
  meta: null,
});

const KEY = "cattrack.userId";
const VIEW_MODE_KEY = "ride-along.viewMode";
const RENTER_LANGUAGE_KEY = "ride-along.renterLanguage";

function readStoredId(): number | null {
  try {
    const v = localStorage.getItem(KEY);
    return v ? Number(v) : null;
  } catch {
    return null;
  }
}

function readStoredViewMode(): "staff" | "renter" {
  try {
    return localStorage.getItem(VIEW_MODE_KEY) === "renter" ? "renter" : "staff";
  } catch {
    return "staff";
  }
}

function readStoredRenterLanguage(): RenterLanguage {
  try {
    const language = localStorage.getItem(RENTER_LANGUAGE_KEY);
    return language === "es" || language === "fr" ? language : "en";
  } catch {
    return "en";
  }
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [users, setUsers] = useState<User[]>([]);
  const [userId, setUserIdState] = useState<number | null>(readStoredId);
  const [viewMode, setViewModeState] = useState<"staff" | "renter">(readStoredViewMode);
  const [renterLanguage, setRenterLanguageState] = useState<RenterLanguage>(readStoredRenterLanguage);
  const [meta, setMeta] = useState<Meta | null>(null);

  const refreshUsers = () => api.users().then(setUsers).catch(() => {});
  useEffect(() => {
    refreshUsers();
    api.meta().then(setMeta).catch(() => {});
  }, []);

  useEffect(() => {
    document.documentElement.lang = viewMode === "renter" ? renterLanguage : "en";
  }, [viewMode, renterLanguage]);

  const setUserId = (id: number) => {
    setUserIdState(id);
    try {
      localStorage.setItem(KEY, String(id));
    } catch {
      /* storage unavailable: selection lasts for this tab only */
    }
  };

  const setViewMode = (mode: "staff" | "renter") => {
    setViewModeState(mode);
    try {
      localStorage.setItem(VIEW_MODE_KEY, mode);
    } catch {
      /* storage unavailable: selection lasts for this tab only */
    }
  };

  const setRenterLanguage = (language: RenterLanguage) => {
    setRenterLanguageState(language);
    try {
      localStorage.setItem(RENTER_LANGUAGE_KEY, language);
    } catch {
      /* storage unavailable: selection lasts for this tab only */
    }
  };

  const user = users.find((u) => u.id === userId) ?? users[0] ?? null;
  return (
    <SessionContext.Provider value={{
      users, user, setUserId, viewMode, setViewMode, renterLanguage, setRenterLanguage, refreshUsers, meta,
    }}>
      {children}
    </SessionContext.Provider>
  );
}

export const useSession = () => useContext(SessionContext);
