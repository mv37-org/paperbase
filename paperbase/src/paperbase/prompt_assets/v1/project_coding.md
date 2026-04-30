The current thread is attached to a Paperbase project.

Treat the project workspace as the source of truth for code work in this thread. Do not guess file contents when project tools can read them.

Python and notebook files should remain runnable and importable. Notebook files are stored as editable percent-format Python source.

Generated runnable code should support smoke-scale validation when practical.

Do not delete project files unless the user explicitly asked for deletion or the change clearly requires removing a superseded path.
