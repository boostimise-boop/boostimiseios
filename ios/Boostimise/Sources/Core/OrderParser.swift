import Foundation

/// Pull order details (name, phones, amount, address) out of a WhatsApp order message -
/// tools/pathao_autofill/order_parser.py. Messages are Bangla/English mixed.
enum OrderParser {
    // Bangla letters for "not followed by a letter": U+0980-U+0982, U+0984-U+09FF
    private static let bnLetters = #"\x{0980}-\x{0982}\x{0984}-\x{09ff}"#
    private static let sepChars = #"[:\x{0983}=\-\x{2013}\x{2014}.\x{0964}]"#

    // Codes are read in the shop's code style (ProductCode.reader()). Any code written with a dash, with the
    // codes after it, so its numbers are not read as the amount (any code, in the other styles).
    private static let codeAnyPrefix = Rx(#"(?<!\w)"# + ProductCode.chatCode.replacingFirst(ProductCode.sep, with: ProductCode.dash),
                                          ignoreCase: true)
    private static let codeAnyLock = NSLock()
    private static var codeAnyOf: (ProductCode.Reader, Rx)?

    private static func codeAny() -> Rx {
        let r = ProductCode.reader()
        if r.kind == "prefix" { return codeAnyPrefix }
        codeAnyLock.lock()
        defer { codeAnyLock.unlock() }
        if let cached = codeAnyOf, cached.0 === r { return cached.1 }
        let rx = Rx(r.chatCode, ignoreCase: true)
        codeAnyOf = (r, rx)
        return rx
    }
    static let phone = Rx(#"(?<!\d)(?:\+?88[ -]?)?(0?1[3-9](?:[ -]?\d){8})(?!\d)"#)
    private static let nameLabel = Rx(
        #"^(?:customer|receiver|recipient|কাস্টমার|গ্রাহক|প্রাপক)?(?:'s|’s|er|ের)?\s*"# +
            #"(?:name|নাম)(?![a-z"# + bnLetters + #"])\s*"# + sepChars + #"*\s*(.*)$"#, ignoreCase: true)
    private static let nameStop = Rx(
        #"(?i)(?<![a-z])(?:mobile|mob|phone|cell|contact|number|address|vill|village|district|thana)(?![a-z])"# +
            #"|মোবাইল|ফোন|নম্বর|নাম্বার|ঠিকানা|গ্রাম|জেলা|থানা|উপজেলা"#)
    private static let brands = "walton|vivo|oppo|samsung|xiaomi|redmi|realme|nokia|symphony|itel|tecno|infinix|huawei|honor|" +
        "nubia|iphone|apple|oneplus|motorola|moto|poco|lava|zte|benco|maximus|helio|pixel|google|sony|" +
        "lenovo|asus|htc|alcatel|micromax|gionee|meizu|primo|xanon|zenix|galaxy|note|pro|max|plus|ultra|" +
        "mini|lite|prime|neo|cover|case"
    private static let notName = Rx(
        #"(?i)জেলা|থানা|উপজেলা|গ্রাম|পোস্ট|পোষ্ট|ডাকঘর|ইউনিয়ন|বাজার|রোড|সদর|মোবাইল|ফোন|নম্বর|নাম্বার|মডেল|টাকা|ঠিকানা|বাকি|দাম"# +
            #"|\b(?:district|dist|zila|zilla|thana|upazila|upozila|village|vill|post|road|house|holding|sadar|bazar|"# +
            "mobile|phone|number|model|tk|taka|due|price|address|cod|total|" + brands + #")\b"#)
    private static let priceStrong = Rx(#"(?i)(?<![a-z])(?:due|cod|total|collect|condition|amount)(?![a-z])|কন্ডিশন|বাকি|কালেক্ট|মোট"#)
    private static let priceWeak = Rx(#"(?i)(?<![a-z])(?:tk|taka|price|bill|bdt)(?![a-z])|টাকা|৳|/-|/=|দাম|মূল্য"#)
    private static let pureNumber = Rx(#"^(?:৳|tk\.?|bdt)?\s*(\d{2,6})\s*(?:tk|taka|টাকা|৳|/-|/=|=|bdt)?\s*\.?$"#, ignoreCase: true)
    private static let number = Rx(#"(?<![A-Za-z\d])(\d{1,3}(?:,\d{3})+|\d+)(?!\d)"#)
    private static let numberAlone = Rx(#"(?<![A-Za-z\d])(\d{1,3}(?:,\d{3})+|\d+)(?![A-Za-z\d])"#)
    private static let phoneLabel = #"(?:(?<![a-z])(?:mobile|mob|phone|cell|contact|whatsapp|number|num|no)(?![a-z])\.?"# +
        #"|মোবাইল|ফোন|নম্বর|নাম্বার|হোয়াটসঅ্যাপ|যোগাযোগ)"#
    private static let phoneWithLabel = Rx("(?:" + phoneLabel + #"\s*"# + sepChars + #"*\s*)*"# + phone.pattern, ignoreCase: true)
    private static let onlyPhoneLabels = Rx("^(?:" + phoneLabel + #"|[\s:\x{0983}=\-\x{2013}\x{2014}.\x{0964},;|/()])*$"#, ignoreCase: true)
    private static let addressLabel = Rx(
        #"^(?:(?:customer|receiver|recipient|delivery|full|কাস্টমার|গ্রাহক|প্রাপক|ডেলিভারি|পূর্ণ|সম্পূর্ণ)"# +
            #"(?:'s|’s|er|ের)?\s*)*(?:address|addr|ঠিকানা)(?![a-z"# + bnLetters + #"])\s*"# + sepChars + #"*\s*"#, ignoreCase: true)
    private static func nfc(_ s: String) -> String { s.precomposedStringWithCanonicalMapping }
    private static let phoneModel = Rx(nfc(
        #"(?i)(?<![a-z])(?:model|walton|vivo|oppo|samsung|xiaomi|redmi|realme|nokia|symphony|itel|tecno|infinix|"# +
            #"huawei|nubia|iphone|oneplus|motorola|moto|poco|lava|zte|benco|maximus|helio|pixel|lenovo|asus|htc|"# +
            #"alcatel|micromax|gionee|meizu|primo|xanon|zenix|galaxy)(?![a-z])"# +
            #"|মডেল|ওয়ালটন|ভিভো|অপো|ওপো|স্যামসাং|শাওমি|রেডমি|রিয়েলমি|নোকিয়া|সিম্ফনি|আইটেল|টেকনো|ইনফিনিক্স|"# +
            #"হুয়াওয়ে|আইফোন|পোকো|মটোরোলা|নুবিয়া"#))
    private static let modelOnly = Rx(#"(?i)^[a-z]{1,3} ?\d{1,3}[a-z]{0,2}\+?(?: (?:pro|max|plus|lite|prime|ultra|neo|power|play|[45]g))*$"#)
    private static let addressHint = Rx(nfc(
        #"(?i)জেলা|থানা|উপজেলা|গ্রাম|পোস্ট|পোষ্ট|ডাকঘর|ইউনিয়ন|বাজার|রোড|সদর|বাসা|বাড়ি|মোড়"# +
            #"|\b(?:district|dist|zila|zilla|thana|upazila|upozila|village|vill|post|road|house|holding|sadar|bazar|"# +
            #"sector|block|lane|area)\b"#))
    private static let edgeChars = " \t,;:|/\\-–—.।ঃ="
    private static let markup = Rx(#"[*~`]|(?<!\d)_|_(?!\d)"#)   // *bold* / _italic_ markers, not "2_152"
    private static let digit = Rx(#"\d"#)
    private static let leadNonWord = Rx(#"^[^\w]+"#)
    private static let tkGlued = Rx(#"(?i)(tk|taka|bdt|৳)(?=\d)"#)
    private static let emptyBrackets = Rx(#"\(\s*\)|\[\s*]"#)
    private static let commas = Rx(#"\s*,[\s,]*"#)
    private static let letter = Rx(#"[^\W\d_]"#)

    static func normalizeCode(_ prefix: String, _ number: String) -> String { ProductCode.norm(prefix, number) }

    static func splitCode(_ code: String) -> (String, Int) { ProductCode.split(code) }

    /// Codes of a list file, in file order, without duplicates or blank lines.
    static func readCodeList(_ text: String) -> [String] {
        let r = ProductCode.reader()
        var seen: [String] = []
        var set = Set<String>()
        for line in text.components(separatedBy: "\n") {
            let l = line.trimEnd("\r")
            guard r.codeParts(l) != nil else { continue }   // a line holding just a code
            let c = r.normalize(l)
            if set.insert(c).inserted { seen.append(c) }
        }
        return seen
    }

    /// Marks a code as done in the list text the way it is done by hand: "3-620<TAB>  01814120910".
    static func writePhoneToList(_ text: String, code: String, phone: String) -> String? {
        let r = ProductCode.reader()
        var lines = text.components(separatedBy: "\n")
        var changed = false
        for i in lines.indices {
            let line = lines[i]
            let body = line.trimEnd("\r")
            guard r.codeParts(body) != nil else { continue }
            if r.normalize(body) == code {
                lines[i] = body.trimEnd() + "\t  " + phone + String(line.dropFirst(body.count))
                changed = true
            }
        }
        return changed ? lines.joined(separator: "\n") : nil
    }

    private static func clean(_ line: String) -> String {
        var l = bnToAscii(line).replacingOccurrences(of: "\u{00a0}", with: " ")
        l = markup.replace(l, "")
        return squeezeSpaces(l).trimmed
    }

    private static func lines(_ text: String?) -> [String] {
        (text ?? "").lines().map { clean($0) }.filter { !$0.isEmpty }
    }

    /// The order codes a message starts with: ["6-872"], ["3-163", "3-164"] for "3-163+164" or "3-163-164", or [].
    static func findCodes(_ text: String?) -> [String] { ProductCode.orderCodes(text) }

    static func findPhones(_ text: String?) -> [String] {
        var phones: [String] = []
        for m in phone.findAll(bnToAscii(text ?? "")) {
            var digits = m.group(1).filter { $0.isASCII && $0.isNumber }
            if digits.count == 10 { digits = "0" + digits }
            if digits.count == 11 && !phones.contains(digits) { phones.append(digits) }
        }
        return phones
    }

    private static func cleanName(_ s0: String) -> String {
        var s = phone.replace(s0, " ")
        if let m = nameStop.find(s) { s = (s as NSString).substring(to: m.start) }
        s = squeezeSpaces(s)
        return s.trimming(" \t,;:|/\\-–—.।ঃ()[]")
    }

    private static func looksLikeName(_ line: String) -> Bool {
        let s = line.trimmed
        return (2...40).contains(s.count) && !digit.contains(s) && !s.contains(",") &&
            spacesRx.split(s).count <= 5 && !notName.contains(s)
    }

    private static func stripLead(_ line: String) -> String { leadNonWord.replace(line, "") }

    private static func findName(_ lines: [String]) -> (String, [Int]) {
        for (i, line) in lines.enumerated() {
            guard let m = nameLabel.find(stripLead(line)) else { continue }
            var name = cleanName(m.group(1))
            var used = [i]
            if name.isEmpty && i + 1 < lines.count && looksLikeName(lines[i + 1]) {
                name = cleanName(lines[i + 1])
                used = [i, i + 1]
            }
            if !name.isEmpty { return (name, used) }
        }
        for (i, line) in lines.prefix(3).enumerated() where looksLikeName(line) { return (cleanName(line), [i]) }
        return ("", [])
    }

    private static func numbers(_ line: String, alone: Bool = false) -> [Int] {
        var t = phone.replace(line, " ")
        t = codeAny().replace(t, " ")
        t = tkGlued.replace(t) { $0.value + " " }
        var out: [Int] = []
        for m in (alone ? numberAlone : number).findAll(t) {
            guard let v = Int64(m.group(1).replacingOccurrences(of: ",", with: "")) else { continue }
            if (10...999_999).contains(v) { out.append(Int(v)) }
        }
        return out
    }

    private struct Amount {
        let value: String
        let guessed: Bool
        let line: Int
    }

    private static func findAmount(_ lines: [String]) -> Amount {
        var best: (rank: Int, line: Int, value: Int)? = nil
        for (i, line) in lines.enumerated() {
            let rank = priceStrong.contains(line) ? 2 : (priceWeak.contains(line) ? 1 : 0)
            if rank == 0 { continue }
            let values = numbers(line)
            if let mx = values.max(), best == nil || rank > best!.rank || (rank == best!.rank && i > best!.line) {
                best = (rank, i, mx)
            }
        }
        if let b = best { return Amount(value: String(b.value), guessed: false, line: b.line) }
        for i in lines.indices.reversed() {
            if let m = pureNumber.matchEntire(lines[i]) { return Amount(value: String(Int(m.group(1)) ?? 0), guessed: false, line: i) }
        }
        for i in lines.indices.reversed().prefix(2) {
            let values = numbers(lines[i], alone: true).filter { $0 >= 50 }
            if let mx = values.max() { return Amount(value: String(mx), guessed: true, line: i) }
        }
        return Amount(value: "", guessed: false, line: -1)
    }

    private static func isPhoneModel(_ s0: String) -> Bool {
        if modelOnly.matches(s0) { return true }
        let s = nfc(s0)
        return phoneModel.contains(s) && spacesRx.split(s).count <= 6 && !addressHint.contains(s)
    }

    private static func findAddress(_ lines: [String], name: String, nameLines: [Int], amount: String, amountLine: Int) -> String {
        var parts: [String] = []
        for (i, line) in lines.enumerated() {
            var s = line
            let m = nameLabel.find(stripLead(s))
            if m != nil || nameLines.contains(i) {
                s = m?.group(1) ?? s
                if !name.isEmpty { s = s.replacingFirst(name, with: " ") }
            }
            if i == amountLine {
                s = priceWeak.replace(priceStrong.replace(s, " "), " ")
                s = number.replace(s) { $0.group(1).replacingOccurrences(of: ",", with: "") == amount ? " " : $0.value }
            }
            s = phoneWithLabel.replace(s, " ")
            s = emptyBrackets.replace(s, " ")
            s = addressLabel.replace(s.trimming(edgeChars), "")
            s = commas.replace(s, ", ")
            s = squeezeSpaces(s).trimming(edgeChars)
            if !letter.contains(s) || onlyPhoneLabels.matches(s) || isPhoneModel(s) { continue }
            if !parts.contains(s) { parts.append(s) }
        }
        return parts.joined(separator: ", ")
    }

    struct Order: Equatable {
        var code: String
        var name: String
        var phone: String
        var phone2: String
        var address: String
        var amount: String
        var warnings: [String]
    }

    static func parseOrder(_ text: String?) -> Order {
        let lines = Self.lines(text)
        let r = ProductCode.reader()
        var code = ""
        var codeLineIndex = -1
        for (i, line) in lines.prefix(3).enumerated() {
            guard let m = r.orderLine(line) else { continue }
            code = normalizeCode(m.group(1), m.group(3))
            codeLineIndex = i
            break
        }
        let body = lines.enumerated().filter { $0.offset != codeLineIndex }.map { $0.element }
        let phones = findPhones(body.joined(separator: "\n"))
        let (name, nameLines) = findName(body)
        let amount = findAmount(body)
        let address = findAddress(body, name: name, nameLines: nameLines, amount: amount.value,
                                  amountLine: amount.guessed ? -1 : amount.line)
        var warnings: [String] = []
        if name.isEmpty { warnings.append("name not found") }
        if phones.isEmpty { warnings.append("phone number not found") }
        if address.isEmpty { warnings.append("address not found") }
        if amount.value.isEmpty { warnings.append("amount not found") }
        else if amount.guessed { warnings.append("amount \(amount.value) is a guess") }
        if phones.count > 2 { warnings.append("\(phones.count) phone numbers in the message, only the first two are used") }
        return Order(code: code, name: name, phone: phones.first ?? "", phone2: phones.count > 1 ? phones[1] : "",
                     address: address, amount: amount.value, warnings: warnings)
    }
}
