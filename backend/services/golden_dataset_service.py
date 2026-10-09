"""
Golden Dataset Service
Parses the golden dataset PDF and compares it with generated test cases
"""
import re
from pathlib import Path
from typing import List, Dict, Optional
import PyPDF2
from difflib import SequenceMatcher


class GoldenDatasetService:
    def __init__(self, pdf_path: str = "Sample-TestCases_HotelApplication.pdf"):
        self.pdf_path = Path(pdf_path)
        self.golden_test_cases: List[Dict] = []
        if self.pdf_path.exists():
            self._parse_pdf()
    
    def _parse_pdf(self):
        """Parse the PDF and extract test cases"""
        try:
            with open(self.pdf_path, 'rb') as file:
                reader = PyPDF2.PdfReader(file)
                text = ""
                for page in reader.pages:
                    text += page.extract_text()
                
                self._extract_test_cases(text)
        except Exception as e:
            print(f"[GoldenDatasetService] Error parsing PDF: {e}")
    
    def _extract_test_cases(self, text: str):
        """Extract test cases from PDF text - handles table-based format"""
        # Clean up text
        text = text.replace('\n', ' ')
        text = re.sub(r'\s+', ' ', text)
        
        # Find test cases by TC-### pattern
        tc_pattern = r'TC-\s*(\d+)\s+(.*?)(?=TC-\s*\d+|$)'
        matches = re.finditer(tc_pattern, text, re.DOTALL)
        
        for match in matches:
            tc_id = f"TC-{match.group(1)}"
            content = match.group(2).strip()
            
            # Extract sections using keywords
            title = ""
            steps = []
            expected_result = ""
            
            # Try to extract objective/title (usually the first meaningful text)
            title_match = re.search(r'(To\s+verify\s+[^.]+|To\s+check\s+[^.]+|Test\s+[^.]+)', content, re.IGNORECASE)
            if title_match:
                title = title_match.group(1).strip()
            
            # Extract steps (numbered list 1., 2., 3., etc.)
            step_matches = re.finditer(r'(\d+\.\s*[^.]+\.)', content)
            for step_match in step_matches:
                step_text = step_match.group(1).strip()
                # Clean up step text
                step_text = re.sub(r'\s+', ' ', step_text)
                if len(step_text) > 10 and not 'URL:' in step_text:  # Filter out test data
                    steps.append(step_text)
            
            # Extract expected result (look for keywords)
            expected_keywords = ['should', 'must', 'expected', 'successfully', 'display', 'shown', 'page', 'message']
            for keyword in expected_keywords:
                pattern = f'({keyword}[^.]+\\.)'
                expected_match = re.search(pattern, content, re.IGNORECASE)
                if expected_match:
                    expected_result = expected_match.group(1).strip()
                    break
            
            # Only add if we have meaningful content
            if title or steps or expected_result:
                self.golden_test_cases.append({
                    'ref_id': tc_id,
                    'title': title if title else f"Test case {tc_id}",
                    'steps': steps[:10],  # Limit to reasonable number of steps
                    'expected_result': expected_result if expected_result else "Expected behavior defined in test case"
                })
        
        print(f"[GoldenDatasetService] Parsed {len(self.golden_test_cases)} golden test cases from PDF")
    
    def calculate_similarity(self, text1: str, text2: str) -> float:
        """Calculate similarity between two texts using SequenceMatcher"""
        if not text1 or not text2:
            return 0.0
        return SequenceMatcher(None, text1.lower(), text2.lower()).ratio()
    
    def compare_test_case(self, test_case: Dict) -> Dict:
        """Compare a generated test case with golden dataset"""
        ref_id = test_case.get('test_case_ref_id') or test_case.get('ref_id') or test_case.get('scenario_ref_id', '')
        
        # Find matching golden test case by ref_id
        # Try exact match first
        golden_tc = None
        for gtc in self.golden_test_cases:
            if gtc['ref_id'] == ref_id:
                golden_tc = gtc
                break
        
        # If no exact match and ref_id is in US##-TC## format, try fuzzy match by comparing test content
        if not golden_tc and ref_id:
            best_match = None
            best_similarity = 0.4  # Minimum threshold for fuzzy match
            
            test_title = test_case.get('title', '').lower()
            test_steps_text = ' '.join(test_case.get('steps', [])).lower()
            
            for gtc in self.golden_test_cases:
                # Calculate similarity based on title and steps
                title_sim = self.calculate_similarity(test_title, gtc['title'].lower())
                steps_text = ' '.join(gtc['steps']).lower()
                steps_sim = self.calculate_similarity(test_steps_text, steps_text)
                
                overall_sim = (title_sim * 0.3 + steps_sim * 0.7)
                
                if overall_sim > best_similarity:
                    best_similarity = overall_sim
                    best_match = gtc
            
            if best_match:
                golden_tc = best_match
        
        if not golden_tc:
            return {
                'match_found': False,
                'similarity_percentage': 0.0,
                'details': 'No matching golden test case found'
            }
        
        # Calculate similarity scores
        title_similarity = self.calculate_similarity(
            test_case.get('title', ''),
            golden_tc['title']
        )
        
        # Compare steps
        generated_steps = test_case.get('steps', [])
        golden_steps = golden_tc['steps']
        
        # Calculate average similarity for steps
        step_similarities = []
        max_steps = max(len(generated_steps), len(golden_steps))
        
        for i in range(max_steps):
            gen_step = generated_steps[i] if i < len(generated_steps) else ""
            gold_step = golden_steps[i] if i < len(golden_steps) else ""
            step_similarities.append(self.calculate_similarity(gen_step, gold_step))
        
        avg_step_similarity = sum(step_similarities) / len(step_similarities) if step_similarities else 0.0
        
        # Compare expected results
        expected_similarity = self.calculate_similarity(
            test_case.get('expected_result', ''),
            golden_tc['expected_result']
        )
        
        # Weighted average: title (20%), steps (60%), expected result (20%)
        overall_similarity = (
            title_similarity * 0.2 +
            avg_step_similarity * 0.6 +
            expected_similarity * 0.2
        )
        
        return {
            'match_found': True,
            'similarity_percentage': round(overall_similarity * 100, 1),
            'title_similarity': round(title_similarity * 100, 1),
            'steps_similarity': round(avg_step_similarity * 100, 1),
            'expected_result_similarity': round(expected_similarity * 100, 1),
            'golden_test_case': {
                'ref_id': golden_tc['ref_id'],
                'title': golden_tc['title']
            }
        }
    
    def get_golden_test_cases(self) -> List[Dict]:
        """Get all parsed golden test cases"""
        return self.golden_test_cases


# Singleton instance
_golden_dataset_service = None


def get_golden_dataset_service() -> GoldenDatasetService:
    """Get or create singleton instance"""
    global _golden_dataset_service
    if _golden_dataset_service is None:
        try:
            _golden_dataset_service = GoldenDatasetService()
        except Exception as e:
            print(f"[GoldenDatasetService] Error creating service: {e}")
            import traceback
            traceback.print_exc()
            # Create a minimal service even if PDF parsing fails
            _golden_dataset_service = GoldenDatasetService.__new__(GoldenDatasetService)
            _golden_dataset_service.golden_test_cases = []
    return _golden_dataset_service
