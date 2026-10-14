# AvatarKit's bundled sheet: Marcel's own look (giraffe, rose, professional — the hub's OWNER_LOOK) drawn by the
# Pixel Zoo (shared/avatar/zoo) at its native 32 × 32. It plays until an agent's own sheet arrives, and offline.
# After the zoo's drawings change, run `make -C ios/AvatarKit sheet` and commit the result; the zoo's tests
# (shared/avatar/zoo/tests/test_app_sheet.py) fail while the copy differs from what the zoo draws.

ZOO := ../../shared/avatar/zoo
DST := $(CURDIR)/Sources/AvatarKit/Resources

.PHONY: sheet

sheet:
	cd $(ZOO) && uv run python -m zoo sheet --animal giraffe --color rose --personality professional --out $(DST)
