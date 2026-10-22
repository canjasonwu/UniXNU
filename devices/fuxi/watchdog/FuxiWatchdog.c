/** @file
  Reset the device a fixed time after DXE starts.

  A finished or failed test leaves the phone showing the UEFI panel, where there
  is no USB function and nothing on the host can reach it; every experiment then
  needs someone to hold the power button. Resetting through PSCI instead brings
  the phone back to Android by itself, so the host can take it to the bootloader
  with adb and start the next image.

  WD_TICKS is substituted at build time by the workflow (100 ns units).
**/
#include <Uefi.h>
#include <Library/UefiBootServicesTableLib.h>
#include <Library/DebugLib.h>

#ifndef WD_TICKS
#define WD_TICKS  1200000000ULL   /* 120 s */
#endif

STATIC EFI_EVENT  mWatchdogTimer;

STATIC VOID EFIAPI
FuxiWatchdogExpire (
  IN EFI_EVENT  Event,
  IN VOID       *Context
  )
{
  DEBUG ((DEBUG_ERROR, "WD expire, PSCI SYSTEM_RESET\n"));
  __asm__ __volatile__ (
    "movz x0, #0x0009\n"
    "movk x0, #0x8400, lsl #16\n"
    "mov  x1, #0\n"
    "mov  x2, #0\n"
    "mov  x3, #0\n"
    "smc  #0\n"
    ::: "x0", "x1", "x2", "x3", "memory");
}

EFI_STATUS EFIAPI
FuxiWatchdogEntry (
  IN EFI_HANDLE        ImageHandle,
  IN EFI_SYSTEM_TABLE  *SystemTable
  )
{
  EFI_STATUS  Status;

  Status = gBS->CreateEvent (EVT_TIMER, TPL_CALLBACK, FuxiWatchdogExpire, NULL, &mWatchdogTimer);
  if (!EFI_ERROR (Status)) {
    Status = gBS->SetTimer (mWatchdogTimer, TimerRelative, WD_TICKS);
  }
  DEBUG ((DEBUG_ERROR, "WD armed: %r\n", Status));
  return EFI_SUCCESS;
}
