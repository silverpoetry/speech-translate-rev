param(
    [string]$ShortcutPath = (Join-Path ([Environment]::GetFolderPath("Desktop")) "Speech Translate.lnk"),
    [string]$PythonwPath = "",
    [string]$ScriptPath = (Join-Path $PSScriptRoot "Run.pyw"),
    [string]$IconPath = (Join-Path $PSScriptRoot "speech_translate\assets\icon.ico"),
    [string]$AppUserModelId = "Dadangdut33.SpeechTranslate.WebviewUI"
)

$ErrorActionPreference = "Stop"

function Initialize-ShortcutPropertyStoreInterop {
    if ("ShortcutPropertyStore" -as [type]) {
        return
    }

    Add-Type -Language CSharp @"
using System;
using System.Runtime.InteropServices;

[ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99")]
internal interface IPropertyStore
{
    uint GetCount(out uint cProps);
    uint GetAt(uint iProp, out PROPERTYKEY pkey);
    uint GetValue(ref PROPERTYKEY key, out PROPVARIANT pv);
    uint SetValue(ref PROPERTYKEY key, ref PROPVARIANT propvar);
    uint Commit();
}

[StructLayout(LayoutKind.Sequential, Pack = 4)]
internal struct PROPERTYKEY
{
    public Guid fmtid;
    public uint pid;
}

[StructLayout(LayoutKind.Explicit)]
internal struct PROPVARIANT
{
    [FieldOffset(0)]
    public ushort vt;

    [FieldOffset(8)]
    public IntPtr pointerValue;

    public static PROPVARIANT FromString(string value)
    {
        return new PROPVARIANT
        {
            vt = 31,
            pointerValue = Marshal.StringToCoTaskMemUni(value),
        };
    }

    public string AsString()
    {
        return pointerValue == IntPtr.Zero ? string.Empty : Marshal.PtrToStringUni(pointerValue) ?? string.Empty;
    }

    public void Clear()
    {
        PropVariantClear(ref this);
    }

    [DllImport("ole32.dll")]
    private static extern int PropVariantClear(ref PROPVARIANT pvar);
}

public static class ShortcutPropertyStore
{
    private static readonly PROPERTYKEY AppUserModelIdKey = new PROPERTYKEY
    {
        fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"),
        pid = 5,
    };

    private static readonly Guid IPropertyStoreGuid = typeof(IPropertyStore).GUID;
    private const uint GpsReadWrite = 0x00000002;
    private const uint GpsDefault = 0x00000000;
    private const ushort VtLpwstr = 31;

    [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
    private static extern void SHGetPropertyStoreFromParsingName(
        string pszPath,
        IntPtr zeroWorks,
        uint flags,
        ref Guid riid,
        [MarshalAs(UnmanagedType.Interface)] out IPropertyStore propertyStore);

    public static void SetAppUserModelId(string shortcutPath, string appUserModelId)
    {
        var propertyStore = OpenPropertyStore(shortcutPath, GpsReadWrite);
        var propVariant = PROPVARIANT.FromString(appUserModelId);
        var propertyKey = AppUserModelIdKey;

        try
        {
            EnsureSucceeded(propertyStore.SetValue(ref propertyKey, ref propVariant));
            EnsureSucceeded(propertyStore.Commit());
        }
        finally
        {
            propVariant.Clear();
        }
    }

    public static string GetAppUserModelId(string shortcutPath)
    {
        var propertyStore = OpenPropertyStore(shortcutPath, GpsDefault);
        var propertyKey = AppUserModelIdKey;
        EnsureSucceeded(propertyStore.GetValue(ref propertyKey, out var propVariant));

        try
        {
            if (propVariant.vt == 0) return string.Empty;
            if (propVariant.vt != VtLpwstr) throw new InvalidOperationException("Shortcut AppUserModelID is not a string.");
            return propVariant.AsString();
        }
        finally
        {
            propVariant.Clear();
        }
    }

    private static IPropertyStore OpenPropertyStore(string shortcutPath, uint flags)
    {
        var propertyStoreGuid = IPropertyStoreGuid;
        SHGetPropertyStoreFromParsingName(shortcutPath, IntPtr.Zero, flags, ref propertyStoreGuid, out var propertyStore);
        return propertyStore;
    }

    private static void EnsureSucceeded(uint hresult)
    {
        if (hresult != 0)
        {
            Marshal.ThrowExceptionForHR(unchecked((int)hresult));
        }
    }
}
"@
}

function Set-ShortcutAppUserModelId {
    param(
        [string]$ShortcutPath,
        [string]$AppUserModelId
    )

    Initialize-ShortcutPropertyStoreInterop
    [ShortcutPropertyStore]::SetAppUserModelId($ShortcutPath, $AppUserModelId)
}

function Get-ShortcutAppUserModelId {
    param([string]$ShortcutPath)

    return (New-Object -ComObject Shell.Application).Namespace((Split-Path -Parent $ShortcutPath)).ParseName((Split-Path -Leaf $ShortcutPath)).ExtendedProperty("System.AppUserModel.ID")
}

function Resolve-PythonwPath {
    param([string]$ProjectRoot)

    $candidates = @(
        (Join-Path $ProjectRoot ".venv314\Scripts\pythonw.exe"),
        (Join-Path $ProjectRoot ".venv\Scripts\pythonw.exe"),
        (Join-Path $ProjectRoot "venv\Scripts\pythonw.exe")
    )

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    $command = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    throw "Unable to locate pythonw.exe. Pass -PythonwPath explicitly."
}

if ([string]::IsNullOrWhiteSpace($PythonwPath)) {
    $PythonwPath = Resolve-PythonwPath -ProjectRoot $PSScriptRoot
} else {
    $PythonwPath = (Resolve-Path -LiteralPath $PythonwPath).Path
}

$ScriptPath = (Resolve-Path -LiteralPath $ScriptPath).Path
$IconPath = (Resolve-Path -LiteralPath $IconPath).Path
$ShortcutPath = [System.IO.Path]::GetFullPath($ShortcutPath)
$ShortcutDir = Split-Path -Parent $ShortcutPath
if ($ShortcutDir -and -not (Test-Path -LiteralPath $ShortcutDir)) {
    New-Item -ItemType Directory -Path $ShortcutDir | Out-Null
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($ShortcutPath)
$shortcut.TargetPath = $PythonwPath
$shortcut.Arguments = ('"{0}"' -f $ScriptPath)
$shortcut.WorkingDirectory = $PSScriptRoot
$shortcut.IconLocation = $IconPath
$shortcut.Description = "Speech Translate (pythonw launcher, AppID: $AppUserModelId)"
$shortcut.Save()
[void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($shortcut)
[void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($shell)
$shortcut = $null
$shell = $null
[GC]::Collect()
[GC]::WaitForPendingFinalizers()
Set-ShortcutAppUserModelId -ShortcutPath $ShortcutPath -AppUserModelId $AppUserModelId

Write-Output "Created shortcut: $ShortcutPath"
Write-Output "Target: $PythonwPath"
Write-Output "Shortcut AppUserModelID written: $AppUserModelId"
Write-Output "Process AppUserModelID is set by speech_translate.__main__ at process startup: $AppUserModelId"
