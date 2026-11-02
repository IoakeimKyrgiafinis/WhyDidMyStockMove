import { Component, type ErrorInfo, type ReactNode } from "react";

interface State {
  error: Error | null;
}

/** Keeps a rendering bug in one part of the page from blanking the whole site. */
/** Give it a `key` that changes with the route, so moving to another page starts it fresh. */
export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("render error", error, info.componentStack);
  }

  render(): ReactNode {
    if (this.state.error) {
      return (
        <div className="status status--error">
          <h2>Something went wrong showing this page.</h2>
          <p className="muted">Try another stock or reload. The error was: {this.state.error.message}</p>
        </div>
      );
    }
    return this.props.children;
  }
}
