; Inno Setup script: packs dist\Boostimise into one Boostimise-Setup-<version>.exe.
; Run build.bat, which passes the name, version, publisher and folders below from core/theme.py.
;
; By default it installs for the current user only (%LOCALAPPDATA%\Programs\Boostimise), so no
; administrator password is needed; the first page lets an administrator install for everyone.
; Settings and WhatsApp / Pathao logins are in %LOCALAPPDATA%\CoverDesk (the app's name up to 2.1)
; and survive updates and uninstalling. An update keeps the folder the old version was installed in
; (...\CasePilot or ...\CoverDesk when updating from those names).

#ifndef AppName
  #error Run build.bat instead of compiling this script directly.
#endif
#define AppExe AppName + ".exe"

[Setup]
; Never change AppId: it is how a new version's setup finds and updates the installed one.
AppId={{C9EF0E5D-F3ED-4C01-81B6-21B3C3BF51DB}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppCopyright=(c) {#AppPublisher}
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppName} Setup
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename={#AppName}-Setup-{#AppVersion}
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
LZMANumBlockThreads=4
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; An update replaces the whole program folder, so no file of the old version is left mixed in.
Type: filesandordirs; Name: "{app}\_internal"
; Up to 2.1 the app was called CoverDesk, up to 3.0 CasePilot: remove their exe and shortcuts when
; updating from them.
Type: files; Name: "{app}\CoverDesk.exe"
Type: files; Name: "{autoprograms}\CoverDesk.lnk"
Type: files; Name: "{autodesktop}\CoverDesk.lnk"
Type: files; Name: "{app}\CasePilot.exe"
Type: files; Name: "{autoprograms}\CasePilot.lnk"
Type: files; Name: "{autodesktop}\CasePilot.lnk"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "{#AppUserModelId}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "{#AppUserModelId}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
