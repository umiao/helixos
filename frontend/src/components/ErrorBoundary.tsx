import React from "react";

interface ErrorBoundaryProps {
  /** Display name for the wrapped region (shown in fallback UI) */
  name: string;
  children: React.ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

/**
 * Reusable React error boundary that catches render errors in child components.
 * Displays a fallback UI with error details and a retry button.
 */
export class ErrorBoundary extends React.Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo): void {
    console.error(
      `[ErrorBoundary:${this.props.name}] Caught error:`,
      error,
      "\nComponent stack:",
      errorInfo.componentStack,
    );
  }

  handleRetry = (): void => {
    this.setState({ hasError: false, error: null });
  };

  render(): React.ReactNode {
    if (this.state.hasError) {
      return (
        <div className="flex flex-col items-center justify-center h-full p-6 bg-white text-center">
          <div className="text-red-500 text-sm font-semibold mb-2">
            {this.props.name} crashed
          </div>
          <div className="text-xs text-gray-500 font-mono max-w-md mb-4 break-words">
            {this.state.error?.message ?? "Unknown error"}
          </div>
          <button
            onClick={this.handleRetry}
            className="px-3 py-1.5 text-xs font-medium text-white bg-indigo-600 rounded hover:bg-indigo-700 transition-colors"
          >
            Retry
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}
