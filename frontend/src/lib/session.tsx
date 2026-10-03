import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, type Meta, type User } from "./api";

type Session = {
  users: User[];
  user: User | null;
  setUserId: (id: number) => void;
  refreshUsers: () => void;
  meta: Meta | null;
};

const SessionContext = createContext<Session>({
  users: [],
  user: null,
  setUserId: () => {},
  refreshUsers: () => {},
  meta: null,
});

const KEY = "cattrack.userId";

function readStoredId(): number | null {
  try {
    const v = localStorage.getItem(KEY);
    return v ? Number(v) : null;
  } catch {
    return null;
  }
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [users, setUsers] = useState<User[]>([]);
  const [userId, setUserIdState] = useState<number | null>(readStoredId);
  const [meta, setMeta] = useState<Meta | null>(null);

  const refreshUsers = () => api.users().then(setUsers).catch(() => {});
  useEffect(() => {
    refreshUsers();
    api.meta().then(setMeta).catch(() => {});
  }, []);

  const setUserId = (id: number) => {
    setUserIdState(id);
    try {
      localStorage.setItem(KEY, String(id));
    } catch {
      /* storage unavailable: selection lasts for this tab only */
    }
  };

  const user = users.find((u) => u.id === userId) ?? users[0] ?? null;
  return (
    <SessionContext.Provider value={{ users, user, setUserId, refreshUsers, meta }}>
      {children}
    </SessionContext.Provider>
  );
}

export const useSession = () => useContext(SessionContext);
