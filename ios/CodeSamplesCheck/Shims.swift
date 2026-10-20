// What the reader's files use from the app's other files (Core/Http.swift), for the check alone.
extension String {
    func ifEmpty(_ other: @autoclosure () -> String) -> String { isEmpty ? other() : self }
}
