import { useEffect, useRef, useState, type ReactNode } from "react";
import { ArrowRight, Loader2 } from "lucide-react";

export type User = { id: string; username: string; display_name: string; role: "admin" | "user" | "observer" };
const notifyTabs = () =>
  localStorage.setItem("balance-auth-event", String(Date.now()));

export function AuthGate({
  children,
}: {
  children: (user: User, logout: () => Promise<void>) => ReactNode;
}) {
  const [user, setUser] = useState<User | null | undefined>(undefined);
  const [error, setError] = useState("");
  const generation = useRef(0);
  const inFlight = useRef(0);
  async function refresh(skipPending = false) {
    if (skipPending && inFlight.current) return;
    inFlight.current++;
    const ticket = ++generation.current;
    try {
      const r = await fetch("/api/auth/me", { cache: "no-store", signal: AbortSignal.timeout(12000) });
      if (ticket !== generation.current) return;
      if (r.status === 401) {
        setUser(null);
        setError("");
        return;
      }
      if (!r.ok) throw new Error("Не удалось проверить сессию");
      const nextUser = await r.json();
      if (ticket === generation.current) {
        setUser(nextUser);
        setError("");
      }
    } catch {
      if (ticket === generation.current)
        setError(
          "Сервер недоступен. Проверьте соединение и повторите попытку.",
        );
    } finally { inFlight.current--; }
  }
  useEffect(() => {
    refresh();
    const expired = () => {
      ++generation.current;
      setUser(null);
    };
    const sync = (e: StorageEvent) => {
      if (e.key === "balance-auth-event") refresh();
    };
    window.addEventListener("balance-session-expired", expired);
    window.addEventListener("storage", sync);
    const synchronize = () => { void refresh(); };
    window.addEventListener("focus", synchronize);
    window.addEventListener("balance-role-changed", synchronize);
    window.addEventListener("balance-profile-changed", synchronize);
    const poll = setInterval(() => { void refresh(true); }, 5000);
    return () => {
      window.removeEventListener("balance-session-expired", expired);
      window.removeEventListener("storage", sync);
      window.removeEventListener("focus", synchronize);
      window.removeEventListener("balance-role-changed", synchronize);
      window.removeEventListener("balance-profile-changed", synchronize);
      clearInterval(poll);
    };
  }, []);
  async function logout() {
    ++generation.current;
    const response = await fetch("/api/auth/logout", {
      method: "POST",
      headers: { "X-Balance-Request": "1" },
    });
    if (!response.ok) throw new Error("Не удалось выйти. Повторите попытку.");
    ++generation.current;
    setUser(null);
    notifyTabs();
  }
  if (user) return <>
    {error && <div className="connection-notice" role="alert">Связь с сервером прервана. Несохранённый ввод остаётся на экране. <button className="text-button" onClick={() => refresh()}>Проверить соединение</button></div>}
    {children(user, logout)}
  </>;
  if (error)
    return (
      <div className="auth-loading">
        <p role="alert">{error}</p>
        <button className="button secondary" onClick={() => refresh()}>
          Повторить
        </button>
      </div>
    );
  if (user === undefined)
    return (
      <div className="auth-loading">
        <Loader2 className="spin" />
        Проверяем сессию…
      </div>
    );
  return (
    <AuthForm
      onSuccess={(u) => {
        ++generation.current;
        setUser(u);
        setError("");
        notifyTabs();
      }}
    />
  );
}

function AuthForm({ onSuccess }: { onSuccess: (user: User) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const r = await fetch("/api/auth/login", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Balance-Request": "1",
        },
        body: JSON.stringify({
          username,
          password,
        }),
      });
      const data = await r.json();
      if (!r.ok)
        throw new Error(
          typeof data.detail === "string"
            ? data.detail
            : "Проверьте логин и пароль.",
        );
      onSuccess(data);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="auth-layout">
      <section className="auth-story">
        <div className="brand">
          <span className="brand-symbol">
            <i />
            <i />
            <i />
          </span>
          <span>
            баланс<span className="brand-sub">платформа</span>
          </span>
        </div>
        <div className="auth-story-body">

          <h1>Анализ изменений</h1>

        </div>

      </section>
      <section className="auth-form-side">
        <div className="auth-card">

          <h2>Вход в систему</h2>
          <form onSubmit={submit}>
            <label className="field">
              Логин
              <input
                required
                minLength={3}
                maxLength={40}
                pattern="[a-zA-Z0-9_.\-]{3,40}"
                autoComplete="username"
                placeholder="например, analyst"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
              />
            </label>
            <label className="field">
              Пароль
              <input
                type="password"
                required
                minLength={10}
                maxLength={128}
                autoComplete="current-password"
                placeholder="Не менее 10 символов"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>
            {error && (
              <div className="error-box" role="alert">
                {error}
              </div>
            )}
            <button className="button auth-submit" disabled={busy}>
              {busy ? (
                <Loader2 className="spin" size={17} />
              ) : (
                <ArrowRight size={17} />
              )}{" "}
              Войти
            </button>
          </form>
        </div>
      </section>
    </div>
  );
}
