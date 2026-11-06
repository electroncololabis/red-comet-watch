#ifndef PackageDir
  #error PackageDir required
#endif
#ifndef ReleaseDir
  #error ReleaseDir required
#endif
#ifndef AppVersion
  #error AppVersion required
#endif
[Setup]
AppId={{D62E7A61-01CC-45D6-9E50-716082FBB761}
AppName=赤色彗星补给战情室
AppVersion={#AppVersion}
AppPublisher=Red Comet Watch
DefaultDirName={localappdata}\Programs\RedCometWatch
DefaultGroupName=赤色彗星补给战情室
AllowNoIcons=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#ReleaseDir}
OutputBaseFilename=RedCometWatch-{#AppVersion}-Windows-x64-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\RedCometWatch.exe
CloseApplications=no
RestartApplications=no

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; Flags: unchecked

[Files]
Source: "{#PackageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userdesktop}\赤色彗星补给战情室（安装版）"; Filename: "{app}\RedCometWatch.exe"; Tasks: desktopicon
Name: "{group}\赤色彗星补给战情室"; Filename: "{app}\RedCometWatch.exe"
Name: "{group}\退出战情室后台"; Filename: "{app}\RedCometWatch.exe"; Parameters: "--stop"
Name: "{group}\打开数据文件夹"; Filename: "{app}\RedCometWatch.exe"; Parameters: "--data-folder"
Name: "{group}\使用说明"; Filename: "{app}\使用说明.txt"
Name: "{group}\卸载"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\RedCometWatch.exe"; Description: "打开赤色彗星补给战情室"; Flags: nowait postinstall skipifsilent unchecked

[Code]
var ScanLock: THandle;

function OpenExistingFile(FileName: String; Access, Share: Cardinal; Security: NativeUInt;
    Disposition, Flags: Cardinal; Template: THandle): THandle;
  external 'CreateFileW@kernel32.dll stdcall';
function LockFile(Handle: THandle; OffsetLow, OffsetHigh, LengthLow, LengthHigh: Cardinal): BOOL;
  external 'LockFile@kernel32.dll stdcall';
function CloseHandle(Handle: THandle): BOOL;
  external 'CloseHandle@kernel32.dll stdcall';

procedure ReleaseScanLock();
begin
  if ScanLock <> 0 then begin
    CloseHandle(ScanLock);
    ScanLock := 0;
  end;
end;

function HoldScanLock(): Boolean;
var DataDir, LockPath: String;
    Handle: THandle;
begin
  Result := True;
  if ScanLock <> 0 then Exit;
  DataDir := GetEnv('HGUC_WATCH_DATA_DIR');
  if DataDir = '' then DataDir := ExpandConstant('{localappdata}\RedCometWatch\data');
  LockPath := AddBackslash(DataDir) + 'tracking.lock';
  if not FileExists(LockPath) then Exit;
  { FileLock locks the first byte. Open the existing file without truncating it. }
  Result := False;
  Handle := OpenExistingFile(LockPath, $80000000, 3, 0, 3, $00200000, 0);
  if Handle = THandle(-1) then Exit;
  if LockFile(Handle, 0, 0, 1, 0) then begin
    ScanLock := Handle;
    Result := True;
  end else CloseHandle(Handle);
end;

function DestinationError(): String;
var PreviousDir, SelectedDir: String;
    Entry: TFindRec;
begin
  Result := '';
  SelectedDir := ExpandConstant('{app}');
  if RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{D62E7A61-01CC-45D6-9E50-716082FBB761}_is1', 'InstallLocation', PreviousDir) then begin
    if CompareText(AddBackslash(SelectedDir), AddBackslash(PreviousDir)) <> 0 then
      Result := '已找到旧版，请选择旧版安装目录进行升级。';
    Exit;
  end;
  if FindFirst(AddBackslash(SelectedDir) + '*', Entry) then begin
    try
      repeat
        if (Entry.Name <> '.') and (Entry.Name <> '..') then begin
          Result := '请选择空目录，避免覆盖其他程序或文件。';
          Break;
        end;
      until not FindNext(Entry);
    finally
      FindClose(Entry);
    end;
  end;
end;

function StopExisting(): Boolean;
var ExitCode: Integer;
begin
  Result := True;
  if FileExists(ExpandConstant('{app}\RedCometWatch.exe')) then
    Result := Exec(ExpandConstant('{app}\RedCometWatch.exe'), '--stop --no-browser', '', SW_HIDE, ewWaitUntilTerminated, ExitCode) and (ExitCode = 0);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := DestinationError();
  if Result <> '' then Exit;
  if FileExists(ExpandConstant('{app}\RedCometWatch.exe')) then begin
    if not StopExisting() then begin
      Result := '请等待正在进行的搜索结束，退出战情室后台后重试。';
      Exit;
    end;
    if not HoldScanLock() then Result := '自动搜索正在运行，请等待完成后再升级。';
  end;
end;

function WantsDataPurge(): Boolean;
var Index: Integer;
begin
  Result := True;
  for Index := 1 to ParamCount do begin
    if CompareText(ParamStr(Index), '/PURGEDATA') = 0 then Exit;
  end;
  Result := False;
  if UninstallSilent() then Exit;
  Result := SuppressibleMsgBox(
    '同时彻底删除本机的战情室数据吗？' + #13#10#13#10 +
    '“是”：删除关注、价格和采购记录、屏蔽、设置、日志，以及战情室专用的闲鱼和拼多多登录状态。此操作无法撤销。' + #13#10 +
    '“否”：只卸载程序并移除它创建的自动任务和快捷方式，保留这些本机数据。',
    mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var Parameters, ErrorMessage: String;
    ExitCode: Integer;
begin
  if CurUninstallStep <> usUninstall then Exit;
  CreateMutex('Local\RedCometWatch.Uninstall');
  Parameters := '--uninstall';
  if WantsDataPurge() then Parameters := Parameters + ' --purge-data';
  ExitCode := -1;
  if Exec(ExpandConstant('{app}\RedCometWatch.exe'), Parameters, '', SW_HIDE,
          ewWaitUntilTerminated, ExitCode) and (ExitCode = 0) then Exit;
  if ExitCode = 2 then
    ErrorMessage := '战情室仍在搜索、启动或使用登录窗口。请等待搜索结束，关闭登录窗口后重试。'
  else
    ErrorMessage := '卸载准备未完成。请关闭战情室窗口并检查数据目录是否可访问、是否有文件被占用，或是否设置了自定义数据目录，然后重试。';
  Log(ErrorMessage);
  SuppressibleMsgBox(ErrorMessage, mbError, MB_OK, IDOK);
  Abort;
end;

procedure DeinitializeSetup();
begin
  ReleaseScanLock();
end;
