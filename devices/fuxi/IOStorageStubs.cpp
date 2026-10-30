/*
 * Link-time stubs for the IOStorageFamily symbols that NativeUfs.cpp references.
 *
 * Why this exists: NativeUfsBlock derives from IOBlockStorageDevice, so its object file references the
 * base class's meta class, constructors, vtable and virtual methods even when the driver is used only
 * through its read path. Those symbols live in the prelinked IOStorageFamily kext, which is not a link
 * input for our own kernel, so the kernel link fails with "Undefined symbols" for them.
 *
 * These stubs exist only to satisfy the linker. The bring-up plan does not start NativeUfs as an
 * IOService: it calls the host/read code procedurally to copy a root image from userdata into RAM and
 * then hands that buffer to the existing RAM-root path. So none of these stubs should ever run; if one
 * does, it returns a failure rather than pretending to succeed.
 *
 * Each definition is pinned to its Itanium-ABI mangled name with an asm label instead of reproducing
 * Apple's class declarations, which keeps this file independent of Apple headers.
 */

#include <stdint.h>

/* Vtable-shaped blob. Never dereferenced by the plan above; zeroed so a stray read cannot interpret
 * heap noise as function pointers. */
__attribute__((aligned(8), used))
uint8_t ioStorageVTable[512] __asm__("__ZTV20IOBlockStorageDevice") = {0};

/* OSMetaClass pointer of the base class. */
__attribute__((used))
uintptr_t ioStorageMetaClass __asm__("__ZN20IOBlockStorageDevice10gMetaClassE") = 0;

void ioBstorCtor1(void *self, const void *meta) __asm__("__ZN20IOBlockStorageDeviceC1EPK11OSMetaClass");
void ioBstorCtor2(void *self, const void *meta) __asm__("__ZN20IOBlockStorageDeviceC2EPK11OSMetaClass");
void ioBstorDtor1(void *self) __asm__("__ZN20IOBlockStorageDeviceD1Ev");
void ioBstorDtor2(void *self) __asm__("__ZN20IOBlockStorageDeviceD2Ev");
void ioBstorDtor0(void *self) __asm__("__ZN20IOBlockStorageDeviceD0Ev");
bool ioBstorInit(void *self, void *dictionary) __asm__("__ZN20IOBlockStorageDevice4initEP12OSDictionary");
void *ioBstorGetProperty(const void *self, const void *symbol)
    __asm__("__ZNK20IOBlockStorageDevice11getPropertyEPK8OSSymbol");
void ioBstorRequestIdle(void *self) __asm__("__ZN20IOBlockStorageDevice11requestIdleEv");
void ioBstorSetProperties(void *self, void *object) __asm__("__ZN20IOBlockStorageDevice13setPropertiesEP8OSObject");
int ioBstorSetPriority(void *self, void *extent, unsigned int a, uint8_t b)
    __asm__("__ZN20IOBlockStorageDevice13doSetPriorityEP26IOBlockStorageDeviceExtentjh");
int ioBstorGetProvisionStatus(void *self, unsigned long long a, unsigned long long b, void *c, void *d)
    __asm__("__ZN20IOBlockStorageDevice20doGetProvisionStatusEyyPjP35IOBlockStorageProvisionDeviceExtenty");
int ioBstorUnmap(void *self, void *extent, unsigned int a, unsigned int b)
    __asm__("__ZN20IOBlockStorageDevice7doUnmapEP26IOBlockStorageDeviceExtentjj");

void ioBstorCtor1(void *, const void *) { }
void ioBstorCtor2(void *, const void *) { }
void ioBstorDtor1(void *) { }
void ioBstorDtor2(void *) { }
void ioBstorDtor0(void *) { }
bool ioBstorInit(void *, void *) { return false; }
void *ioBstorGetProperty(const void *, const void *) { return nullptr; }
void ioBstorRequestIdle(void *) { }
void ioBstorSetProperties(void *, void *) { }
int ioBstorSetPriority(void *, void *, unsigned int, uint8_t) { return 0; }
int ioBstorGetProvisionStatus(void *, unsigned long long, unsigned long long, void *, void *) { return 0; }
int ioBstorUnmap(void *, void *, unsigned int, unsigned int) { return 0; }

/* BootProgress's logging globals. NativeUfs.cpp records probe results through them; the component is
 * off in this configuration (CONFIG_XNU_BOOT_PROGRESS=n), so provide zeroed storage. When boot progress
 * is enabled these definitions must be dropped, otherwise the real ones collide. */
extern "C" {
    unsigned long long boot_log_errors = 0;
    unsigned long long boot_log_first_lba = 0;
    unsigned long long boot_log_rounds = 0;
    unsigned long long boot_log_state = 0;
    unsigned long long boot_log_tick = 0;
}
