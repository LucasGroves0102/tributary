import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  /** when this changes (the page in the address bar) a failed page gets another try */
  resetKey?: string;
  /** the whole app failed: fill the window; otherwise it fills the page, and the shell stays */
  whole?: boolean;
  onReload?: () => void;
}

/** What shows instead of a blank window when rendering fails (a malformed API body, a bug): a
 * plain message and a way out. It says nothing about the error: that goes to the console. */
export class ErrorBoundary extends Component<Props, { failed: boolean }> {
  override state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  override componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(error, info.componentStack);
  }

  override componentDidUpdate(previous: Props) {
    if (this.state.failed && previous.resetKey !== this.props.resetKey) this.setState({ failed: false });
  }

  override render() {
    if (!this.state.failed) return this.props.children;
    const reload = this.props.onReload ?? (() => window.location.reload());
    const body = (
      <section className="card pad crash" role="alert">
        <h1>This page didn't load</h1>
        <p className="muted">Something on it went wrong on our side. Your saved work isn't affected. Reloading usually fixes it.</p>
        <div className="row">
          <button className="btn primary" onClick={reload}>Reload</button>
          <a className="btn" href="#/">Go to Home</a>
        </div>
      </section>
    );
    return this.props.whole ? <main className="welcome">{body}</main> : <div className="page">{body}</div>;
  }
}
