/* A relocatable launcher: application code and its runtime stay together. */
#define UNICODE
#define _UNICODE
#include <windows.h>
#include <wchar.h>
#include <stdio.h>

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR arguments, int show) {
    wchar_t root[32768], path[32768], command[32768], runtime[32768];
    if (!GetModuleFileNameW(NULL, root, 32768)) return 1;
    wchar_t *separator = wcsrchr(root, L'\\');
    if (!separator) return 1;
    *separator = 0;
    SetEnvironmentVariableW(L"NABRIA_INSTALL_DIR", root);
    swprintf(runtime, 32768, L"%ls\\runtime", root);
    SetEnvironmentVariableW(L"PYTHONHOME", runtime);
    swprintf(path, 32768, L"%ls\\cert.pem", runtime);
    if (GetFileAttributesW(path) != INVALID_FILE_ATTRIBUTES) SetEnvironmentVariableW(L"SSL_CERT_FILE", path);
    swprintf(path, 32768, L"%ls\\app", root);
    SetEnvironmentVariableW(L"PYTHONPATH", path);
    swprintf(path, 32768, L"%ls\\lib\\girepository-1.0", runtime);
    SetEnvironmentVariableW(L"GI_TYPELIB_PATH", path);
    swprintf(path, 32768, L"%ls\\share", runtime);
    SetEnvironmentVariableW(L"XDG_DATA_DIRS", path);
    swprintf(path, 32768, L"%ls\\bin;%ls\\engine;C:\\Windows\\System32;C:\\Windows", runtime, root);
    SetEnvironmentVariableW(L"PATH", path);
    swprintf(path, 32768, L"%ls\\bin\\pythonw.exe", runtime);
    if (swprintf(command, 32768, L"\"%ls\" -m nabria %ls", path, arguments) < 0) return 1;
    STARTUPINFOW startup = {0};
    PROCESS_INFORMATION process = {0};
    startup.cb = sizeof(startup);
    if (!CreateProcessW(path, command, NULL, NULL, FALSE, CREATE_NO_WINDOW, NULL, root, &startup, &process)) {
        MessageBoxW(NULL, L"Nabria could not start. Please reinstall the application.", L"Nabria", MB_OK | MB_ICONERROR);
        return 1;
    }
    CloseHandle(process.hThread);
    WaitForSingleObject(process.hProcess, INFINITE);
    DWORD result = 1;
    GetExitCodeProcess(process.hProcess, &result);
    CloseHandle(process.hProcess);
    return (int)result;
}
