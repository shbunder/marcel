import Foundation
import MarcelKit
import Observation

/// One line in a notebook section: a note, or a project that has no notes yet.
public struct NoteRow: Hashable, Sendable, Identifiable {
    public var path: MemoryPath
    public var title: String
    /// nil for a note that does not exist yet (opening it starts a new one).
    public var updatedAt: Date?
    /// There are unsaved changes for it on this phone.
    public var hasDraft: Bool

    public var id: MemoryPath { path }
}

/// The notebook's list (wireframe 6B): "About you" with its first lines, project notes, dated notes, other notes,
/// and what Marcel carries.
@MainActor
@Observable
public final class NotebookModel {
    public enum State: Equatable {
        case loading
        case loaded
        case notAvailableYet
        case failed(String)
    }

    public private(set) var state: State = .loading
    /// `MEMORY.md`'s row, always there (opening it creates it when the hub has none).
    public private(set) var aboutYou: NoteRow
    /// "· Prefers mornings for appointments", the first lines of `MEMORY.md`.
    public private(set) var aboutYouPreview: [String] = []
    /// One per project with notes, then the projects without any, by name.
    public private(set) var projects: [NoteRow] = []
    /// Newest first.
    public private(set) var dated: [NoteRow] = []
    public private(set) var others: [NoteRow] = []
    /// How many files the hub listed under a name the app will not open.
    public private(set) var refused = 0
    /// A problem with a part of the list (the "About you" lines), shown at the top; tap to clear.
    public var problem: String?

    @ObservationIgnored let service: any NotebookService
    @ObservationIgnored public let drafts: DraftStore
    @ObservationIgnored public let rollovers: RolloverLog
    @ObservationIgnored let projectNames: @MainActor () -> [String: String]
    @ObservationIgnored public let text: NotebookText

    /// - Parameter projectNames: slug → name of every project (from the cache), so project notes show the project's
    ///   name and a project without notes can get some.
    public init(service: any NotebookService, drafts: DraftStore = DraftStore(), rollovers: RolloverLog,
                projectNames: @escaping @MainActor () -> [String: String] = { [:] }, text: NotebookText = NotebookText()) {
        self.service = service
        self.drafts = drafts
        self.rollovers = rollovers
        self.projectNames = projectNames
        self.text = text
        aboutYou = NoteRow(path: .aboutYou, title: "About you", updatedAt: nil, hasDraft: false)
    }

    public var names: [String: String] { projectNames() }

    public func load() async {
        let listing: MemoryListing
        do {
            listing = try await service.listing()
        } catch {
            state = (error as? NotebookError) == .notAvailableYet ? .notAvailableYet : .failed(NotebookError.words(for: error))
            refreshDrafts()
            return
        }
        let names = projectNames()
        let withDraft = drafts.paths
        func row(_ entry: MemoryEntry) -> NoteRow {
            NoteRow(path: entry.path, title: text.title(entry.path, projectNames: names), updatedAt: entry.updatedAt,
                    hasDraft: withDraft.contains(entry.path))
        }
        var aboutYou = NoteRow(path: .aboutYou, title: "About you", updatedAt: nil, hasDraft: withDraft.contains(.aboutYou))
        var projects: [NoteRow] = [], dated: [(Date, NoteRow)] = [], others: [NoteRow] = []
        for entry in listing.files {
            switch entry.path.section {
            case .aboutYou: aboutYou = row(entry)
            case .project: projects.append(row(entry))
            case .dated(let day): dated.append((day, row(entry)))
            case .other: others.append(row(entry))
            }
        }
        let noted = Set(projects.map(\.path))
        let empty = names.compactMap { slug, name -> NoteRow? in
            guard let path = MemoryPath.projectNotes(slug: slug), !noted.contains(path) else { return nil }
            return NoteRow(path: path, title: name, updatedAt: nil, hasDraft: withDraft.contains(path))
        }
        let byName: (NoteRow, NoteRow) -> Bool = {
            $0.title.localizedCaseInsensitiveCompare($1.title) == .orderedAscending
        }
        self.aboutYou = aboutYou
        self.projects = projects.sorted(by: byName) + empty.sorted(by: byName)
        self.dated = dated.sorted { $0.0 > $1.0 }.map(\.1)
        self.others = others.sorted { $0.path < $1.path }
        refused = listing.refused
        state = .loaded
        problem = nil

        await dropDraftsTheHubAlreadyHas(listing)
        aboutYouPreview = []
        if aboutYou.updatedAt != nil {
            do {
                aboutYouPreview = text.preview(try await service.file(.aboutYou).content)
            } catch {
                problem = NotebookError.words(for: error)
            }
        }
    }

    /// A draft whose words are what the hub now has (saved from another phone, or Marcel wrote the same) is not
    /// "unsaved changes": it is thrown away, whatever version it started from. Reads only notes that have a draft.
    private func dropDraftsTheHubAlreadyHas(_ listing: MemoryListing) async {
        let listed = Set(listing.files.map(\.path))
        for path in drafts.paths where listed.contains(path) {
            guard let draft = drafts.draft(for: path),
                  let current = try? await service.file(path), current.content == draft.text else { continue }
            drafts.discard(path)
        }
        refreshDrafts()
    }

    /// Marks the rows that have a draft (after coming back from a note).
    public func refreshDrafts() {
        let withDraft = drafts.paths
        aboutYou.hasDraft = withDraft.contains(aboutYou.path)
        for index in projects.indices { projects[index].hasDraft = withDraft.contains(projects[index].path) }
        for index in dated.indices { dated[index].hasDraft = withDraft.contains(dated[index].path) }
        for index in others.indices { others[index].hasDraft = withDraft.contains(others[index].path) }
    }

    /// The editor for a row.
    public func editor(for row: NoteRow) -> NoteEditorModel {
        NoteEditorModel(path: row.path, title: row.title, service: service, drafts: drafts, text: text)
    }

    /// The row's second line: "Updated yesterday 18:12 · unsaved changes", "No notes yet".
    public func detail(_ row: NoteRow) -> String {
        var parts = [row.updatedAt.map { "Updated \(text.when($0))" } ?? "No notes yet"]
        if row.hasDraft { parts.append("unsaved changes") }
        return parts.joined(separator: " · ")
    }
}

/// One note open for reading and editing: a preview, a save that sends `If-Match`, "changed elsewhere" with the hub's
/// version and a diff, and a draft that keeps unsaved text on this phone until it is saved or thrown away.
@MainActor
@Observable
public final class NoteEditorModel {
    public enum Mode: String, Equatable, CaseIterable, Sendable {
        case edit, preview
    }

    public enum State: Equatable {
        case loading
        case ready
        case failed(String)
    }

    public let path: MemoryPath
    public let title: String
    public private(set) var state: State = .loading
    public var mode: Mode = .preview
    /// The version the text started from (nil: a new note, or one that was removed).
    public private(set) var file: MemoryFile?
    /// The etag a save sends as `If-Match`; nil creates the file.
    public private(set) var base: String?
    /// What is in the editor. Every change is kept as a draft on this phone.
    public var text = "" {
        didSet {
            guard state == .ready, text != oldValue else { return }
            keepDraft()
        }
    }

    /// The hub's version after a save came back 409, or when a draft turned out to start from an older version.
    /// While it is set, Save is off: the person reloads the hub's version or saves theirs over it, knowingly.
    public private(set) var conflict: MemoryFile?
    /// What went wrong last, in plain words; tap to clear.
    public var problem: String?
    /// A plain note: "Saved.", "Your unsaved changes from yesterday 18:12 are back."
    public var notice: String?
    public private(set) var saving = false
    /// The note was opened from its draft because the hub could not be reached: when that draft was last kept.
    public private(set) var draftOnly: Date?

    @ObservationIgnored let service: any NotebookService
    @ObservationIgnored let drafts: DraftStore
    @ObservationIgnored public let words: NotebookText
    /// When a draft is kept (fixed in tests).
    @ObservationIgnored let clock: () -> Date

    public init(path: MemoryPath, title: String, service: any NotebookService, drafts: DraftStore,
                text: NotebookText = NotebookText(), clock: @escaping () -> Date = { Date() }) {
        self.path = path
        self.title = title
        self.service = service
        self.drafts = drafts
        self.words = text
        self.clock = clock
    }

    /// The text as the hub has it (empty for a new note).
    public var saved: String { file?.content ?? "" }

    /// There is something to save.
    public var hasChanges: Bool { text != saved }

    public var canSave: Bool { state == .ready && hasChanges && conflict == nil && !saving }

    /// "Updated yesterday 18:12 by Marcel", "Not saved yet: the hub can't be reached…", "New note…".
    public var line: String {
        if let file { return words.updated(file) }
        if let draftOnly {
            return "Not saved yet: the hub can't be reached. Your draft from \(words.when(draftOnly))."
        }
        return "New note: saving creates it"
    }

    /// The diff shown under "changed elsewhere": the hub's lines against yours.
    public var diff: [LineDiff.Line] {
        conflict.map { LineDiff.folded(theirs: $0.content, yours: text) } ?? []
    }

    public func load() async {
        let stored = drafts.draft(for: path)
        var current: MemoryFile?
        do {
            current = try await service.file(path)
        } catch NotebookError.notFound {
            current = nil
        } catch {
            guard let draft = stored else {
                state = .failed(NotebookError.words(for: error))
                return
            }
            // Offline (or the hub down) with a draft: show it, so the person can keep going; a save later still
            // checks against the version the draft started from.
            file = nil
            base = draft.base
            draftOnly = draft.at
            text = draft.text
            state = .ready
            mode = .edit
            problem = NotebookError.words(for: error)
            return
        }
        file = current
        base = current?.etag
        draftOnly = nil
        if let stored, let current, stored.text == current.content {
            drafts.discard(path)  // the hub already has these words (saved elsewhere, or typed back): nothing unsaved
        }
        guard let draft = drafts.draft(for: path) else {
            text = current?.content ?? ""
            state = .ready
            mode = current == nil ? .edit : .preview
            return
        }
        // A draft from before: bring it back (before `state` is ready, so it is not re-kept with a new base). If the
        // note changed since the draft began, say so now, and keep the draft's own base so leaving and coming back
        // shows the conflict again instead of losing it.
        text = draft.text
        state = .ready
        mode = .edit
        notice = "Your unsaved changes from \(words.when(draft.at)) are back."
        if let current, draft.base != current.etag, draft.text != current.content {
            conflict = current
            base = draft.base
        } else if current == nil, draft.base != nil {
            problem = NotebookError.removed.errorDescription
        }
    }

    /// Saves with `If-Match: <base>`. A 409 shows the hub's version and the diff; the draft stays until the person
    /// chooses.
    public func save() async {
        guard canSave else { return }
        saving = true
        defer { saving = false }
        notice = nil
        do {
            switch try await service.save(path, content: text, ifMatch: base) {
            case .saved(let now):
                file = now
                base = now.etag
                drafts.discard(path)
                problem = nil
                notice = "Saved."
                if text != now.content { keepDraft() }  // typed on while it was saving
            case .changedElsewhere(let theirs):
                conflict = theirs
                problem = nil
            }
        } catch NotebookError.removed {
            // Removed since it was opened: the next save creates it again.
            file = nil
            base = nil
            keepDraft()
            problem = NotebookError.removed.errorDescription
        } catch {
            problem = NotebookError.words(for: error)
        }
    }

    /// "Reload": throws away this phone's changes and shows the hub's version.
    public func reloadTheirs() {
        guard let theirs = conflict else { return }
        conflict = nil
        file = theirs
        base = theirs.etag
        text = theirs.content
        drafts.discard(path)
        problem = nil
        notice = "Showing the hub's version. Your changes were thrown away."
    }

    /// "Save mine over it": a knowing overwrite, against the version that was just shown (so a third change made in
    /// the meantime still comes back as a conflict).
    public func saveMineOverTheirs() async {
        guard let theirs = conflict else { return }
        conflict = nil
        file = theirs
        base = theirs.etag
        keepDraft()
        await save()
    }

    /// Throws away this phone's changes (no conflict).
    public func discardChanges() {
        text = saved
        drafts.discard(path)
        notice = nil
    }

    private func keepDraft() {
        if text == saved {
            drafts.discard(path)
        } else if let words = drafts.keep(Draft(text: text, base: base, at: clock()), for: path).words {
            problem = words
        }
    }
}
