; Inno Setup スクリプト。build_installer.bat から
; iscc /DAppVersion=1.2.0 setup.iss の形で呼ばれる想定。

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName "特典台帳"
#define AppExeName "TokutenDaicho.exe"
#define AppPublisher "injeharu"

[Setup]
AppId={{A1F3E9C2-5B7D-4E1A-9C3F-2D8B6A4E7F10}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\TokutenDaicho
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; 管理者権限を求めず、ユーザーごとのフォルダにインストールする
PrivilegesRequired=lowest
OutputDir=..\dist_installer
OutputBaseFilename=TokutenDaicho-Setup-v{#AppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#AppExeName}
SetupIconFile=
LanguageDetectionMethod=locale

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"

[Tasks]
Name: "desktopicon"; Description: "デスクトップにアイコンを作成する"; GroupDescription: "追加のアイコン:"

[Files]
Source: "..\dist\TokutenDaicho\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\アンインストール {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{#AppName} を今すぐ起動する"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; アンインストール時、プログラム本体は消すが視聴者データ(%APPDATA%側)は残す。
; ここにはプログラムフォルダ内の一時生成物のみ対象にする。
Type: filesandordirs; Name: "{app}\__pycache__"

[UninstallRun]
; アプリ側で登録した「PC起動時に自動起動」の残骸を掃除する
; (残すと、存在しないexeをWindowsが毎回起動しようとするため)
Filename: "reg.exe"; Parameters: "delete HKCU\Software\Microsoft\Windows\CurrentVersion\Run /v TokutenDaicho /f"; Flags: runhidden; RunOnceId: "RemoveAutostart"
