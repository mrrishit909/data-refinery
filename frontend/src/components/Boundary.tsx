import { Component, type ReactNode } from "react";

/** Keeps one broken station from blanking the whole workbench; shows what failed and offers a retry. */
export class StageBoundary extends Component<{ children: ReactNode; label: string }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="dr-panel" role="alert" style={{ display: "grid", gap: 10 }}>
        <p className="dr-h3">The {this.props.label} station could not be drawn.</p>
        <p className="dr-small">{this.state.error.message}</p>
        <div><button type="button" className="dr-btn" onClick={() => this.setState({ error: null })}>Try again</button></div>
      </div>
    );
  }
}
