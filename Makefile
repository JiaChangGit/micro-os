PYTHON ?= python3
PHASE ?= P0
IMAGE ?= build/kernel.img
TIMEOUT ?= 45
TEST_ARGS ?=
CROSS ?= aarch64-linux-gnu-
CFLAGS := -std=c11 -O2 -g -ffreestanding -fno-builtin -fno-pie -fno-stack-protector -mgeneral-regs-only -mstrict-align -Wall -Wextra -Werror -Iinclude -Ibuild -MMD -MP
SOURCES := kernel/main.c kernel/console.c kernel/string.c kernel/fdt.c kernel/exception.c kernel/task.c kernel/state.c arch/aarch64/vm.c arch/aarch64/irq.c
OBJECTS := $(SOURCES:%.c=build/%.o) build/arch/aarch64/boot.o build/arch/aarch64/vectors.o
USER_ELFS := build/user/root.elf build/user/rtc.elf build/user/client.elf

.DEFAULT_GOAL := help
.DELETE_ON_ERROR:
.SECONDARY: $(USER_ELFS:.elf=.o) build/user/start.o
.PHONY: help all check lint test test-tools test-host test-p0 test-p1 test-p2 test-p3 test-p4 test-p5 test-p6 test-p7 demo rebuild toolcheck toolcheck-target context phase-status phase-check qemu install-skill

all: build/kernel.img build/platform.dtb

build/%.o: %.c
	@mkdir -p $(@D)
	$(CROSS)gcc $(CFLAGS) -c $< -o $@

build/%.o: %.S
	@mkdir -p $(@D)
	$(CROSS)gcc $(CFLAGS) -c $< -o $@

build/kernel.elf: $(OBJECTS) linker/kernel.ld
	$(CROSS)ld -z max-page-size=4096 -T linker/kernel.ld -Map=build/kernel.map -o $@ $(OBJECTS)

build/kernel.img: build/kernel.elf
	$(CROSS)objcopy -O binary $< $@

build/user/%.elf: build/user/%.o build/user/start.o user/linker.ld
	$(CROSS)ld -z max-page-size=4096 -T user/linker.ld -o $@ build/user/start.o $<

build/user_images.h: $(USER_ELFS) tools/pack_users.py
	$(PYTHON) tools/pack_users.py $(USER_ELFS) --output $@

build/kernel/task.o: build/user_images.h

build/platform.dtb: config/qemu.json
	@mkdir -p build
	qemu-system-aarch64 -machine virt,gic-version=3,virtualization=off,highmem=off,dumpdtb=$@ -cpu cortex-a53 -accel tcg -smp 1 -m 512M -display none -nic none

test-host: build/platform.dtb
	@mkdir -p build/host
	gcc -std=c11 -O1 -g -Wall -Wextra -Werror -fsanitize=address,undefined -Iinclude kernel/fdt.c tests/host/test_fdt.c -o build/host/test-fdt
	build/host/test-fdt build/platform.dtb

test-p0: all test-host
	$(PYTHON) tools/gates.py P0

test-p1: all
	$(PYTHON) tools/gates.py P1

test-p2 test-p3 test-p4 test-p5 test-p6 test-p7: all
	$(PYTHON) tools/gates.py $(patsubst test-p%,P%,$@)

test: check all test-host
	$(PYTHON) tools/gates.py P0
	$(PYTHON) tools/gates.py P1
	$(PYTHON) tools/gates.py P2
	$(PYTHON) tools/gates.py P3
	$(PYTHON) tools/gates.py P4
	$(PYTHON) tools/gates.py P5
	$(PYTHON) tools/gates.py P6
	$(PYTHON) tools/gates.py P7
	$(PYTHON) tools/phase.py check P7

rebuild:
	$(MAKE) -B all

demo: all
	$(PYTHON) tools/run_qemu.py --case p7-demo --image build/kernel.img --boot-args case=p7-demo --show-output

-include $(OBJECTS:.o=.d) $(USER_ELFS:.elf=.d) build/user/start.d

help:
	@$(PYTHON) -c "print('make all | rebuild | test | demo\nmake test-p0 ... test-p7 | check | toolcheck-target\nmake context PHASE=P0 | phase-status | phase-check PHASE=P7\nmake qemu TEST=p6-pending\npython3 tools/debug_qemu.py --case p6-pending\nmake install-skill SKILLS_DIR=/path/to/skills')"

check: lint test-tools

lint:
	$(PYTHON) tools/check.py

test-tools:
	$(PYTHON) -m unittest discover -s tests $(TEST_ARGS)

toolcheck:
	$(PYTHON) tools/preflight.py --profile host

toolcheck-target:
	$(PYTHON) tools/preflight.py --profile target --output build/reports/environment.json

context:
	$(PYTHON) tools/context.py --phase "$(PHASE)"

phase-status:
	$(PYTHON) tools/phase.py status

phase-check:
	$(PYTHON) tools/phase.py check "$(PHASE)"

qemu: all
	@test -n "$(TEST)" || { echo 'Set TEST, for example TEST=p0-boot'; exit 2; }
	$(PYTHON) tools/run_qemu.py --case "$(TEST)" --image "$(IMAGE)" --timeout "$(TIMEOUT)" --boot-args "case=$(TEST)" --show-output

install-skill:
ifdef SKILLS_DIR
	$(PYTHON) tools/install_skill.py --target "$(SKILLS_DIR)"
else
	$(PYTHON) tools/install_skill.py
endif
