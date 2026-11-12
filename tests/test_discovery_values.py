import unittest

from discovery_mvp.values import prepare_report, with_tender_value


class TenderValueTests(unittest.TestCase):
    def item(self, text, score=.9, title='Opportunity'):
        return {'title': title, 'snippets': [text], 'score': score}

    def test_indian_units_and_rupee_forms_are_normalized(self):
        for text, expected in [
            ('Estimated value ₹11.97 crore', 119700000),
            ('Estimated value: ₹34.80 lakh', 3480000),
            ('Tender value Rs. 3,75,109', 375109),
            ('Estimated cost INR 2.54 crores', 25400000),
            ('Project value: 26.8 lakhs', 2680000),
            ('Contract value INR 2 million', 2000000),
        ]:
            with self.subTest(text=text):
                self.assertEqual(with_tender_value(self.item(text))['tender_value_inr'], expected)

    def test_emd_fees_unit_prices_and_unknowns_are_not_project_values(self):
        for text in ['EMD ₹15,000; tender fee ₹2,000', 'Five nozzles at ₹15,000 each',
                     'Estimated cost not disclosed; EMD ₹50,000', 'Estimated value: 2026',
                     'Estimated value: USD 5 million']:
            with self.subTest(text=text):
                self.assertIsNone(with_tender_value(self.item(text))['tender_value_inr'])

    def test_conflicting_sources_do_not_silently_pick_highest_price(self):
        item = self.item('Estimated value ₹1 crore')
        item['snippets'].append('Tender value ₹2 crore')
        valued = with_tender_value(item)
        self.assertIsNone(valued['tender_value_inr'])
        self.assertEqual(valued['tender_value_status'], 'conflicting')
        self.assertEqual(len(valued['tender_value_evidence']), 2)

    def test_same_amount_in_different_units_is_not_conflict(self):
        item = self.item('Estimated value ₹1 crore')
        item['snippets'].append('Estimated cost INR 100 lakh')
        self.assertEqual(with_tender_value(item)['tender_value_inr'], 10000000)

    def test_match_is_primary_then_price_and_unknowns_are_last_within_match(self):
        items = [self.item('Estimated value ₹100 crore', .8, 'Lower match, huge value'),
                 self.item('No disclosed value', .9, 'Unknown'),
                 self.item('Estimated value ₹1 crore', .9, 'Smaller'),
                 self.item('Estimated value ₹3 crore', .9, 'Bigger'),
                 self.item('No disclosed value', 1, 'Highest match')]
        report = prepare_report({'candidates': items})
        self.assertEqual([item['title'] for item in report['candidates']],
                         ['Highest match', 'Bigger', 'Smaller', 'Unknown', 'Lower match, huge value'])
        self.assertNotIn('tender_value_inr', items[0])


if __name__ == '__main__':
    unittest.main()
