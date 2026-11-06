#!/usr/bin/env bash
# This file is tri-licensed under GPLv2, Apache 2.0, or the SQLite Blessing.
# You may choose any of these licenses to use this code.
#
# In place of a legal notice, here is a blessing:
#  - May you do good and not evil.
#  - May you find forgiveness for yourself and forgive others.
#  - May you share freely, never taking more than you give.
#
# SPDX-License-Identifier: GPL-2.0-only OR Apache-2.0 OR blessing

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKDIR="${WORKDIR:-$ROOT_DIR/work}"
ARTIFACT_DIR="$WORKDIR/artifacts"
KERNEL_DIR="$WORKDIR/munch-kernel"
ANYKERNEL_DIR="$WORKDIR/AnyKernel3"
NONGKI_DIR="$WORKDIR/non-gki-build"
CLANG_PREBUILT_DIR="$WORKDIR/aosp-clang"
KERNEL_REPO="${KERNEL_REPO:-https://github.com/MiCode/Xiaomi_Kernel_OpenSource.git}"
RESUKISU_REPO="${RESUKISU_REPO:-https://github.com/ReSukiSU/ReSukiSU.git}"
NONGKI_REPO="${NONGKI_REPO:-https://github.com/JackA1ltman/NonGKI_Kernel_Build_2nd.git}"
ANYKERNEL_REPO="${ANYKERNEL_REPO:-https://github.com/osm0sis/AnyKernel3.git}"
AOSP_CLANG_REPO="${AOSP_CLANG_REPO:-https://android.googlesource.com/platform/prebuilts/clang/host/linux-x86}"
AOSP_CLANG_REF="${AOSP_CLANG_REF:-android-msm-redbull-4.19-android12-qpr3}"
AOSP_CLANG_VERSION="${AOSP_CLANG_VERSION:-clang-r399163b}"
USE_AOSP_CLANG="${USE_AOSP_CLANG:-0}"
KERNEL_REF="${KERNEL_REF:-munch-s-oss}"
RESUKISU_REF="${RESUKISU_REF:-main}"
NONGKI_REF="${NONGKI_REF:-mainline}"
ARCH="${ARCH:-arm64}"
DEFCONFIG="${DEFCONFIG:-munch_user_defconfig}"
CROSS_COMPILE="${CROSS_COMPILE:-aarch64-linux-gnu-}"
CROSS_COMPILE_COMPAT="${CROSS_COMPILE_COMPAT:-arm-linux-gnueabi-}"
OBJCOPY="${OBJCOPY:-${CROSS_COMPILE}objcopy}"
OBJDUMP="${OBJDUMP:-${CROSS_COMPILE}objdump}"
KERNEL_LD="${KERNEL_LD:-${CROSS_COMPILE}ld.bfd}"
KERNEL_LD_COMPAT="${KERNEL_LD_COMPAT:-${CROSS_COMPILE_COMPAT}ld.bfd}"
LLVM_IAS="${LLVM_IAS:-0}"
REAL_CC="${REAL_CC:-clang}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
JOBS="${JOBS:-$(nproc)}"
ZIP_NAME="${ZIP_NAME:-munch-miui14-resukisu.zip}"

mkdir -p "$WORKDIR"
rm -rf "$KERNEL_DIR" "$ANYKERNEL_DIR" "$NONGKI_DIR" "$ARTIFACT_DIR"
mkdir -p "$ARTIFACT_DIR"

if [[ "$USE_AOSP_CLANG" == "1" ]]; then
  rm -rf "$CLANG_PREBUILT_DIR"
  git clone --depth 1 --branch "$AOSP_CLANG_REF" "$AOSP_CLANG_REPO" "$CLANG_PREBUILT_DIR"
  export PATH="$CLANG_PREBUILT_DIR/$AOSP_CLANG_VERSION/bin:$PATH"
fi

git clone --depth 1 --branch "$KERNEL_REF" "$KERNEL_REPO" "$KERNEL_DIR"
git clone --depth 1 --branch "$RESUKISU_REF" "$RESUKISU_REPO" "$KERNEL_DIR/KernelSU"
git clone --depth 1 --branch "$NONGKI_REF" "$NONGKI_REPO" "$NONGKI_DIR"

pushd "$KERNEL_DIR" >/dev/null
bash KernelSU/kernel/setup.sh "$RESUKISU_REF"
git apply "$ROOT_DIR/patches/gcc-wrapper-py3.patch"
git apply "$ROOT_DIR/patches/aw8697-print-pattern-pointer.patch"
patch -p1 -F3 < "$NONGKI_DIR/Patches/Patch/susfs_patch_to_4.19.patch"
git apply "$ROOT_DIR/patches/non-gki-susfs-munch-fixes.patch"
git apply "$ROOT_DIR/patches/bq2597x-dev-role-cast.patch"
git apply "$ROOT_DIR/patches/ln8000-dev-role-cast.patch"
git apply "$ROOT_DIR/patches/drm-sysfs-mi-weak-stubs.patch"
git apply "$ROOT_DIR/patches/selinux-state-no-rticdata.patch"
bash "$NONGKI_DIR/Patches/susfs_inline_hook_patches.sh"
popd >/dev/null

make -C "$KERNEL_DIR" O="$WORKDIR/out" ARCH="$ARCH" \
  LLVM=1 LLVM_IAS="$LLVM_IAS" REAL_CC="$REAL_CC" PYTHON="$PYTHON_BIN" \
  LD="$KERNEL_LD" LD_COMPAT="$KERNEL_LD_COMPAT" \
  OBJCOPY="$OBJCOPY" OBJDUMP="$OBJDUMP" \
  CROSS_COMPILE="$CROSS_COMPILE" CROSS_COMPILE_COMPAT="$CROSS_COMPILE_COMPAT" \
  "$DEFCONFIG"

"$KERNEL_DIR/scripts/config" --file "$WORKDIR/out/.config" \
  --enable KSU \
  --enable KSU_SUSFS \
  --disable KSU_TRACEPOINT_HOOK \
  --disable KSU_MANUAL_HOOK \
  --disable KPM \
  --disable RANDOMIZE_BASE \
  --disable RELOCATABLE

# --- baseline behaviour alignment: ak3_k40s-ksu@Xinran6.21.zip ---
# Keeps 4.2 + SUSFS + inline hooks untouched; aligns only runtime-visible
# policy/feature deltas present in this kernel tree. Symbols absent from the
# tree are silently dropped by olddefconfig, so this is safe across variants.
# Only generic upstream symbols are disabled here. Vendor-private debug symbols
# (MSM_TZ_LOG, CORESIGHT, QTI_*, ...) are referenced as `#if CONFIG_FOO` in
# munch-s-oss sources; removing them triggers -Wundef warnings that the
# forbidden-warning mechanism turns into a hard build failure.
"$KERNEL_DIR/scripts/config" --file "$WORKDIR/out/.config" \
  --enable KSU_TOOLKIT_SUPPORT \
  --enable MODULE_FORCE_LOAD \
  --enable SUSPEND_SKIP_SYNC \
  --enable TCP_CONG_ADVANCED \
  --enable TCP_CONG_BBR \
  --enable TCP_CONG_WESTWOOD \
  --enable NET_SCH_DEFAULT \
  --enable NET_SCH_FQ_CODEL \
  --enable DEFAULT_BBR \
  --enable DEFAULT_FQ_CODEL \
  --enable HZ_300 \
  --enable EROFS_FS \
  --enable EROFS_FS_POSIX_ACL \
  --enable EROFS_FS_SECURITY \
  --enable EROFS_FS_XATTR \
  --enable EROFS_FS_ZIP \
  --enable WIREGUARD \
  --enable NTFS_FS \
  --enable NTFS_RW \
  --enable DM_CRYPT \
  --enable DM_USER \
  --enable ZRAM_WRITEBACK \
  --enable USB_UAS \
  --enable USB_F_UVC \
  --enable USB_CONFIGFS_F_UVC \
  --enable HIDRAW \
  --enable HID_PLAYSTATION \
  --enable SONY_FF \
  --enable PLAYSTATION_FF \
  --enable XFRM_MIGRATE \
  --enable IP_NF_TARGET_TTL \
  --enable NETFILTER_XT_TARGET_HL \
  --enable UNICODE \
  --enable TOUCHSCREEN_NT36XXX \
  --enable TOUCHSCREEN_COMMON \
  --enable FINGERPRINT_FPC_TEE \
  --enable FINGERPRINT_GOODIX \
  --enable ARM64_LSE_ATOMICS \
  --enable ARM_DSU_PMU \
  --enable ARM64_ERRATUM_1742098 \
  --enable ARM64_ERRATUM_3194386 \
  --enable MITIGATE_SPECTRE_BRANCH_HISTORY \
  --enable BOEFFLA_WL_BLOCKER \
  --enable BUILD_ARM64_DT_OVERLAY \
  --enable CRYPTO_LZ4 \
  --enable CRYPTO_SM3 \
  --enable CRYPTO_SM4 \
  --enable CRYPTO_CHACHA20_NEON \
  --enable CRYPTO_CRC32_ARM64_CE \
  --enable CRYPTO_POLY1305_NEON \
  --enable CRYPTO_SM3_ARM64_CE \
  --enable CRYPTO_SM4_ARM64_CE \
  --enable CRYPTO_LIB_CHACHA \
  --enable CRYPTO_LIB_POLY1305 \
  --enable CRYPTO_LIB_CURVE25519 \
  --enable CRYPTO_LIB_BLAKE2S \
  --enable CRYPTO_LIB_CHACHA20POLY1305 \
  --enable EXFAT_FS \
  --enable F2FS_IOSTAT \
  --enable FS_HPB \
  --enable UFSHPB_31 \
  --enable UFSTW_31 \
  --disable DYNAMIC_DEBUG \
  --disable TASK_DELAY_ACCT \
  --disable FORTIFY_SOURCE \
  --disable UNMAP_KERNEL_AT_EL0 \
  --disable HARDEN_BRANCH_PREDICTOR \
  --disable CRYPTO_LZO \
  --disable CRYPTO_CHACHA20 \
  --disable CRYPTO_CHACHA20POLY1305 \
  --disable CRYPTO_POLY1305 \
  --disable HZ_250 \
  --set-str DEFAULT_TCP_CONG "bbr" \
  --set-str DEFAULT_NET_SCH "fq_codel" \
  --set-str STATIC_USERMODEHELPER_PATH "" \
  --set-str LOCALVERSION "-rc" \
  --set-str KSU_FULL_NAME_FORMAT "%TAG_NAME%-%COMMIT_SHA%@xinran" \
  --set-val ION_POOL_FILL_MARK 48 \
  --set-val SPLIT_PTLOCK_CPUS 999999


make -C "$KERNEL_DIR" O="$WORKDIR/out" ARCH="$ARCH" \
  LLVM=1 LLVM_IAS="$LLVM_IAS" REAL_CC="$REAL_CC" PYTHON="$PYTHON_BIN" \
  LD="$KERNEL_LD" LD_COMPAT="$KERNEL_LD_COMPAT" \
  OBJCOPY="$OBJCOPY" OBJDUMP="$OBJDUMP" \
  CROSS_COMPILE="$CROSS_COMPILE" CROSS_COMPILE_COMPAT="$CROSS_COMPILE_COMPAT" \
  olddefconfig

make -C "$KERNEL_DIR" O="$WORKDIR/out" ARCH="$ARCH" \
  LLVM=1 LLVM_IAS="$LLVM_IAS" REAL_CC="$REAL_CC" PYTHON="$PYTHON_BIN" \
  LD="$KERNEL_LD" LD_COMPAT="$KERNEL_LD_COMPAT" \
  OBJCOPY="$OBJCOPY" OBJDUMP="$OBJDUMP" \
  CROSS_COMPILE="$CROSS_COMPILE" CROSS_COMPILE_COMPAT="$CROSS_COMPILE_COMPAT" \
  -j"$JOBS" Image

test -s "$WORKDIR/out/arch/$ARCH/boot/Image"
test -s "$WORKDIR/out/System.map"

git clone --depth 1 "$ANYKERNEL_REPO" "$ANYKERNEL_DIR"

source "$ROOT_DIR/packaging/anykernel.env"
cat > "$ANYKERNEL_DIR/anykernel.sh" <<EOF
### AnyKernel3 Ramdisk Mod Script
## autogenerated for Redmi K40S / munch

properties() { '
kernel.string=$KERNEL_STRING
do.devicecheck=$DO_DEVICECHECK
do.modules=$DO_MODULES
do.systemless=$DO_SYSTEMLESS
do.cleanup=$DO_CLEANUP
do.cleanuponabort=$DO_CLEANUPONABORT
device.name1=$DEVICE_NAME_1
device.name2=$DEVICE_NAME_2
device.name3=$DEVICE_NAME_3
supported.versions=$SUPPORTED_VERSIONS
supported.patchlevels=
supported.vendorpatchlevels=
'; }

boot_attributes() {
set_perm_recursive 0 0 755 644 \$RAMDISK/*
set_perm_recursive 0 0 750 750 \$RAMDISK/init* \$RAMDISK/sbin
}

BLOCK=$BLOCK_DEVICE
IS_SLOT_DEVICE=$IS_SLOT_DEVICE
RAMDISK_COMPRESSION=auto
PATCH_VBMETA_FLAG=auto

. tools/ak3-core.sh

dump_boot
write_boot
EOF

cp "$WORKDIR/out/arch/$ARCH/boot/Image" "$ANYKERNEL_DIR/Image"
(cd "$ANYKERNEL_DIR" && zip -r9 "$ARTIFACT_DIR/$ZIP_NAME" . -x '*.git*')
cp "$WORKDIR/out/arch/$ARCH/boot/Image" "$ARTIFACT_DIR/Image"
cp "$WORKDIR/out/.config" "$ARTIFACT_DIR/kernel.config"
cp "$WORKDIR/out/System.map" "$ARTIFACT_DIR/System.map"

cat > "$ARTIFACT_DIR/build-info.txt" <<EOF
kernel_ref=$KERNEL_REF
kernel_sha=$(git -C "$KERNEL_DIR" rev-parse HEAD)
resukisu_ref=$RESUKISU_REF
resukisu_sha=$(git -C "$KERNEL_DIR/KernelSU" rev-parse HEAD)
nongki_ref=$NONGKI_REF
nongki_sha=$(git -C "$NONGKI_DIR" rev-parse HEAD)
use_aosp_clang=$USE_AOSP_CLANG
aosp_clang_ref=$AOSP_CLANG_REF
aosp_clang_version=$AOSP_CLANG_VERSION
objcopy=$OBJCOPY
objdump=$OBJDUMP
kernel_ld=$KERNEL_LD
kernel_ld_compat=$KERNEL_LD_COMPAT
llvm_ias=$LLVM_IAS
zip_name=$ZIP_NAME
display_mi=fallback-weak-stubs
EOF

bash "$ROOT_DIR/scripts/verify-artifact.sh" "$ARTIFACT_DIR" main

echo "Artifacts are in $ARTIFACT_DIR"
