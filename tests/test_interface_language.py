import json
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree

import seo


class InterfaceLanguageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = (Path(__file__).resolve().parents[1] / "static" / "index.html").read_text(encoding="utf-8")

    def homepage(self, lang):
        with patch("seo.verify_meta", return_value='<meta name="google-site-verification" content="test">'), \
             patch("seo.paysettings.load_pay", return_value={"plans": []}):
            return seo.index_html(self.template, lang)

    def test_homepages_identify_each_other_and_keep_verification(self):
        for lang, path in (("ru", "/"), ("en", "/en/")):
            with self.subTest(lang=lang):
                html = self.homepage(lang)
                self.assertIn(f'<html lang="{lang}">', html)
                self.assertIn(f'<link rel="canonical" href="{seo.SITE}{path}">', html)
                self.assertIn(f'<meta property="og:url" content="{seo.SITE}{path}">', html)
                for alternate, target in (("ru", "/"), ("en", "/en/"), ("x-default", "/")):
                    self.assertIn(f'<link rel="alternate" hreflang="{alternate}" href="{seo.SITE}{target}">', html)
                self.assertIn('name="google-site-verification" content="test"', html)
                data = html.split('<script type="application/ld+json">', 1)[1].split("</script>", 1)[0]
                self.assertEqual(json.loads(data)["inLanguage"], lang)

    def test_english_labels_and_switch_keep_form_identifiers(self):
        english = self.homepage("en")
        russian = self.homepage("ru")
        self.assertIn('data-ui-lang="en" aria-label="English" aria-current="true"', english)
        self.assertIn('data-ui-lang="ru" aria-label="Русский" aria-current="true"', russian)
        self.assertIn('id="gateTitle">Access required<', english)
        self.assertIn('id="gateTitle">Нужен доступ<', russian)
        self.assertIn('id="searchForm"', english)
        self.assertIn('data-go="thumbs"', english)
        self.assertIn('Find a YouTube niche', english)

    def test_english_homepage_is_discoverable(self):
        locs = ElementTree.fromstring(seo.sitemap()).findall(".//{*}loc")
        self.assertIn(f"{seo.SITE}/en/", [node.text for node in locs])
        self.assertIn(f"{seo.SITE}/en/", seo.urls())
