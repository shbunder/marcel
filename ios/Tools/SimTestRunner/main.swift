// Runs a SwiftPM-built .xctest bundle inside the iOS simulator, with the Keychain entitlement an app would
// have (the stock `xctest` agent has none, so Keychain calls fail with -34018). Built and used by
// `make -C ios check`; see ios/README.md for why `xcodebuild test` is not used on this Mac.
// XCTest prints each test and failure itself. Usage: SimTestRunner <bundle.xctest> [TestClass ...]
import Foundation
import XCTest

let arguments = CommandLine.arguments
guard arguments.count >= 2, let bundle = Bundle(path: arguments[1]), bundle.load() else {
    print("error: could not load the test bundle \(arguments.dropFirst().first ?? "(none)")")
    exit(2)
}
let all = XCTestSuite.default
let wanted = Set(arguments.dropFirst(2))
let suite: XCTestSuite
if wanted.isEmpty {
    suite = all
} else {
    suite = XCTestSuite(name: "Selected")
    func collect(_ test: XCTest) {
        if let group = test as? XCTestSuite, !wanted.contains(group.name) {
            group.tests.forEach(collect)
        } else if wanted.contains(test.name) || wanted.contains(String(describing: type(of: test))) {
            suite.addTest(test)
        }
    }
    collect(all)
}
suite.run()
let run = suite.testRun!
print("Executed \(run.executionCount) tests, with \(run.skipCount) skipped and \(run.totalFailureCount) failures.")
exit(run.hasSucceeded && run.executionCount > 0 ? 0 : 1)
