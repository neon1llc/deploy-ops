# Local infrastructure input

The directory adapter creates a named local resource marker. It does not provision Docker or a virtual
machine. The application depends on this receipt so an app-only release can reuse it without another
infrastructure operation. Replace this component with your own reviewed infrastructure contract when needed.
