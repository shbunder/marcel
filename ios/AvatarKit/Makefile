# AvatarKit's copy of the sprite sheet. The source of truth is shared/avatar/pixel/out/ (S-13.1, build.py).
# After the giraffe changes there, run `make -C ios/AvatarKit sheet` and commit the copies;
# SheetResourceTests fails while they differ.

SRC := ../../shared/avatar/pixel/out
DST := Sources/AvatarKit/Resources

.PHONY: sheet

sheet:
	cp $(SRC)/giraffe.png $(SRC)/giraffe.json $(DST)/
