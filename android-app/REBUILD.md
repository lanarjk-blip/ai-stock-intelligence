# R91 Android rebuild

Requested 2026-10-25 from main baseline 1eb5c5a98c88ddd08575009be12916ce250c5d20.

Restore mobile_ui_version to 1.8.58, matching the already distributed Android shell; main had regressed to1.8.57. No logo, package identity, model, or backend behavior changes. The existing Android APK Build workflow produces the artifact. Physical Android acceptance and model accuracy limitations remain in validation/acceptance/REPORT.md.
