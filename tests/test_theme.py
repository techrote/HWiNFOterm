import unittest

from hwinfo_term.theme import Theme, make_theme, theme_document


class ThemeValidationTests(unittest.TestCase):
    def test_malformed_interpolation_in_saved_theme_falls_back(self):
        for invalid in ([], {}, None, 5):
            with self.subTest(value=invalid):
                theme = make_theme("LUTEX Night", {
                    "theme": "LUTEX Night", "palette": {"interpolation": invalid},
                })
                self.assertEqual(theme.palette["interpolation"], "srgb")
                self.assertEqual(len(theme.lut), 256)
                self.assertEqual(make_theme("LUTEX Night", theme_document(theme)).lut, theme.lut)

    def test_malformed_semantic_roles_in_definition_fall_back(self):
        for invalid in ([], None, "invalid"):
            with self.subTest(value=invalid):
                theme = Theme("LUTEX Night", {"semantic_roles": invalid})
                self.assertEqual(theme.colour("text.primary"), "#e5edf3")

    def test_colour_fallback_is_deterministic_for_repeated_frames(self):
        for mode, white in (("256", "\x1b[38;5;15m"), ("16", "\x1b[97m")):
            theme = make_theme("LUTEX Night", colour_mode=mode)
            self.assertEqual(theme.ansi("#ffffff"), white)
            first = [theme.ansi(colour) for colour in theme.lut]
            self.assertEqual(first, [theme.ansi(colour) for colour in theme.lut])


if __name__ == "__main__":
    unittest.main()
