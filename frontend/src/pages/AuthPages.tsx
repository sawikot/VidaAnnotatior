import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Navigate, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { MaterialIcon } from "../components/MaterialIcon";
import { Button } from "../components/primitives";
import { Field } from "../components/formControls";
import { getPasswordLink, login, setupAdmin, submitPasswordLink, type AuthUser } from "../services/api";
import { useAuthStore } from "../stores/authStore";

const MIN_PASSWORD = 8;

/** The centred card every sign-in page shares. */
function AuthCard({ title, subtitle, children }: { title: string; subtitle?: ReactNode; children: ReactNode }) {
  return (
    <div className="min-h-screen bg-[#0f172a] flex items-center justify-center p-space-lg">
      <div className="w-full max-w-sm bg-surface-container-lowest rounded-xl shadow-xl p-space-lg flex flex-col gap-space-md">
        <div className="flex items-center gap-space-sm">
          <img src="/favicon.svg" alt="" className="h-9 w-9" />
          <div className="leading-tight">
            <div className="font-headline-sm text-headline-sm">VirtualPatch WSI Annotator</div>
            <div className="text-body-sm text-on-surface-variant">Digital Pathology Research Suite</div>
          </div>
        </div>
        <div>
          <h1 className="font-headline-md text-headline-md">{title}</h1>
          {subtitle && <p className="text-body-sm text-on-surface-variant mt-1">{subtitle}</p>}
        </div>
        {children}
      </div>
    </div>
  );
}

function ErrorLine({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <div role="alert" className="flex items-start gap-1.5 text-body-sm text-error">
      <MaterialIcon name="error" className="!text-[18px] shrink-0" />
      {message}
    </div>
  );
}

/** Where to go after signing in: back where the person was sent from, else the projects. */
function useAfterSignIn() {
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from;
  return from && from !== "/login" ? from : "/projects";
}

export function LoginPage() {
  const state = useAuthStore((s) => s.state);
  const signedIn = useAuthStore((s) => s.signedIn);
  const next = useAfterSignIn();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (state === "signed-in") return <Navigate to={next} replace />;
  if (state === "setup") return <Navigate to="/setup" replace />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      signedIn(await login(email, password));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not sign in");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthCard title="Sign in">
      <form onSubmit={submit} className="flex flex-col gap-space-md">
        <Field label="Email">
          <input className="input" type="email" autoComplete="username" autoFocus required value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field label="Password">
          <input className="input" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <ErrorLine message={error} />
        <Button variant="primary" type="submit" disabled={busy} className="justify-center">
          {busy ? "Signing in..." : "Sign in"}
        </Button>
        <p className="text-body-sm text-on-surface-variant">Forgot your password or have no account? Ask your administrator for a link.</p>
      </form>
    </AuthCard>
  );
}

/** New password + confirmation, with the length rule shown as the person types. */
function PasswordFields({ password, confirm, onPassword, onConfirm }: { password: string; confirm: string; onPassword: (v: string) => void; onConfirm: (v: string) => void }) {
  return (
    <>
      <Field label={`Password (at least ${MIN_PASSWORD} characters)`}>
        <input className="input" type="password" autoComplete="new-password" required value={password} onChange={(e) => onPassword(e.target.value)} />
      </Field>
      <Field label="Repeat the password">
        <input className="input" type="password" autoComplete="new-password" required value={confirm} onChange={(e) => onConfirm(e.target.value)} />
      </Field>
    </>
  );
}

function passwordFormProblem(password: string, confirm: string): string | null {
  if (password.length < MIN_PASSWORD) return `The password must be at least ${MIN_PASSWORD} characters.`;
  if (password !== confirm) return "The two passwords are not the same.";
  return null;
}

/** First run: there are no users yet, so whoever installs the app creates the first administrator. */
export function SetupPage() {
  const state = useAuthStore((s) => s.state);
  const signedIn = useAuthStore((s) => s.signedIn);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (state === "signed-in") return <Navigate to="/projects" replace />;
  if (state === "signed-out") return <Navigate to="/login" replace />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    const problem = passwordFormProblem(password, confirm);
    if (problem) return setError(problem);
    setBusy(true);
    setError(null);
    try {
      signedIn(await setupAdmin({ name, email, password }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create the account");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthCard
      title="Create the administrator"
      subtitle="The app has no users yet. This first account manages users and sees every project, including those made before accounts existed."
    >
      <form onSubmit={submit} className="flex flex-col gap-space-md">
        <Field label="Your name">
          <input className="input" autoFocus required value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Email">
          <input className="input" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <PasswordFields password={password} confirm={confirm} onPassword={setPassword} onConfirm={setConfirm} />
        <ErrorLine message={error} />
        <Button variant="primary" type="submit" disabled={busy} className="justify-center">
          {busy ? "Creating..." : "Create administrator"}
        </Button>
      </form>
    </AuthCard>
  );
}

/** Opened from a one-time link an administrator passed on: set (or reset) the password. */
export function SetPasswordPage() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const signedIn = useAuthStore((s) => s.signedIn);
  const navigate = useNavigate();
  const [person, setPerson] = useState<AuthUser | null>(null);
  const [linkError, setLinkError] = useState<string | null>(token ? null : "This link is incomplete.");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    getPasswordLink(token)
      .then(setPerson)
      .catch((e) => setLinkError(e instanceof Error ? e.message : "This link does not work."));
  }, [token]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const problem = passwordFormProblem(password, confirm);
    if (problem) return setError(problem);
    setBusy(true);
    setError(null);
    try {
      signedIn(await submitPasswordLink(token, password));
      navigate("/projects", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not set the password");
    } finally {
      setBusy(false);
    }
  }

  if (linkError) {
    return (
      <AuthCard title="Link not valid">
        <ErrorLine message={linkError} />
        <Button onClick={() => navigate("/login")} className="justify-center">
          Go to sign in
        </Button>
      </AuthCard>
    );
  }
  if (!person) return <AuthCard title="Checking the link...">{null}</AuthCard>;

  return (
    <AuthCard title={person.has_password ? "Choose a new password" : "Welcome"} subtitle={`${person.name} · ${person.email}`}>
      <form onSubmit={submit} className="flex flex-col gap-space-md">
        <PasswordFields password={password} confirm={confirm} onPassword={setPassword} onConfirm={setConfirm} />
        <ErrorLine message={error} />
        <Button variant="primary" type="submit" disabled={busy} className="justify-center">
          {busy ? "Saving..." : "Set password and sign in"}
        </Button>
      </form>
    </AuthCard>
  );
}
