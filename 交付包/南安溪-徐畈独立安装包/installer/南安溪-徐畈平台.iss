#define AppName "南安溪-徐畈水文平台"
#define AppVersion "1.2.4"
#define AppExe "南安溪徐畈平台.exe"
[Setup]
AppId={{C3B19B34-1229-4C69-981F-0EB1785D91C3}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\Nananxi-Xufan-Platform
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
OutputDir={#GetEnv('XUFAN_OUTPUT_DIR')}
OutputBaseFilename=南安溪-徐畈独立安装包-删除记录修复版-v1.2.4-Setup
ArchitecturesInstallIn64BitMode=x64
Compression=lzma/fast
SolidCompression=yes
WizardStyle=modern
Uninstallable=yes
UninstallFilesDir={app}\uninstall
UninstallDisplayIcon={app}\{#AppExe}
[Files]
Source: "{#GetEnv('XUFAN_APP_SOURCE')}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#GetEnv('XUFAN_SEED_SOURCE')}\*"; DestDir: "{app}\seed\runtime"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "start_platform.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "..\使用说明.md"; DestDir: "{app}"; Flags: ignoreversion
[Icons]
Name: "{userprograms}\{#AppName}"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\installer\start_platform.ps1"""; WorkingDir: "{app}"
Name: "{userdesktop}\南安溪-徐畈平台"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\installer\start_platform.ps1"""; WorkingDir: "{app}"; Tasks: desktopicon
[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："
[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then MsgBox('卸载程序不会删除安装目录中的 workspace 用户数据。', mbInformation, MB_OK);
end;
