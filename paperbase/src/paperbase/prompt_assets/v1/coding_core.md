Project workspace tools operate on the current project's repo-relative text files.

Use project_list_files and project_grep to discover files, project_read_file to inspect content, project_apply_patch or project_write_file to change files, and project_move or project_delete only when a rename or removal is clearly required.

Prefer minimal patches over full rewrites when updating existing files.

Use project_git_status or project_git_diff when the changed-file set is unclear.

Use project_exec, project_wait, project_logs, project_test, project_lint, or project_run when validation is helpful.
