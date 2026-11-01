#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
gcc -O3 -shared -fPIC -mcpu=cortex-a9 -Wall -Wextra -Werror -o native/libpscnn_frame.so native/native_frame.c
gcc -O2 -shared -fPIC -Wall -Wextra -Werror -o native/libpscnn_mmio.so native/ordered_mmio.c
gcc -O2 -shared -fPIC -Wall -Wextra -Werror -DPSCNN_MOCK -o native/libpscnn_frame_mock.so native/native_frame.c native/frame_mock.c
gcc -O3 -shared -fPIC -mcpu=cortex-a9 -mfpu=neon -Wall -Wextra -Werror -o native/libpscnn_preprocess.so native/exact_gray.c native/exact_resize.c -ljpeg -lm
