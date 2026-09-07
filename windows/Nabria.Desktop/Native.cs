using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Interop;

namespace Nabria.Desktop;

internal static class Native
{
    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    internal static extern int SetCurrentProcessExplicitAppUserModelID(string id);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern IntPtr CreateJobObject(IntPtr attributes, string? name);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool SetInformationJobObject(IntPtr job, int infoClass, IntPtr data, uint length);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll")] private static extern bool CloseHandle(IntPtr handle);
    [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW")] private static extern IntPtr GetWindowLong(IntPtr window, int index);
    [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW")] private static extern IntPtr SetWindowLong(IntPtr window, int index, IntPtr value);
    [DllImport("user32.dll")] internal static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] internal static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);

    [DllImport("kernel32.dll")] internal static extern IntPtr GetConsoleWindow();

    internal sealed class Job : IDisposable
    {
        private IntPtr handle = CreateJobObject(IntPtr.Zero, null);
        public Job()
        {
            IntPtr data = Marshal.AllocHGlobal(144);
            try
            {
                Marshal.Copy(new byte[144], 0, data, 144);
                Marshal.WriteInt32(data, 16, 0x2000);
                if (handle == IntPtr.Zero || !SetInformationJobObject(handle, 9, data, 144))
                    throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            finally { Marshal.FreeHGlobal(data); }
        }
        public void Assign(Process process)
        {
            if (AssignProcessToJobObject(handle, process.Handle)) return;
            int error = Marshal.GetLastWin32Error();
            process.Kill(entireProcessTree: true);
            throw new Win32Exception(error);
        }
        public void Dispose() { if (handle != IntPtr.Zero) { CloseHandle(handle); handle = IntPtr.Zero; } }
    }

    internal static void NoActivate(Window window)
    {
        var handle = new WindowInteropHelper(window).Handle;
        SetWindowLong(handle, -20, new IntPtr(GetWindowLong(handle, -20).ToInt64() | 0x08000000 | 0x80));
        HwndSource.FromHwnd(handle)?.AddHook((IntPtr hwnd, int message, IntPtr w, IntPtr l, ref bool handled) =>
        { if (message == 0x21) { handled = true; return new IntPtr(3); } return IntPtr.Zero; });
    }
}
