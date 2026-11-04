# src/deploy_ops/__main__.py - Share the package and installed command entry points.


from .cli import main

# Use the same exit status as the installed command
raise SystemExit(main())
