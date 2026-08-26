"""Policy chunking strategy extending existing infrastructure."""

from typing import List, Dict, Any
import re
from backend.infrastructure.chunking.base_strategy import IChunkingStrategy


class PolicyChunkingStrategy(IChunkingStrategy):
    """Legal-aware chunking for policy documents using existing infrastructure."""

    def __init__(self, min_chunk_size: int = 20, max_chunk_size: int = 1000):
        # Chunking here is rule/sentence-boundary driven, not size-bounded
        # (unlike sentence/paragraph/section/clause strategies) - these
        # exist for IChunkingStrategy/ChunkingFactory(**kwargs) interface
        # compatibility. min_chunk_size matches the existing 20-char
        # skip-short-sentence threshold in _extract_rules_from_section.
        self.min_chunk_size = min_chunk_size
        self.max_chunk_size = max_chunk_size

    def get_chunk_size(self) -> int:
        """Return the target chunk size."""
        return self.max_chunk_size

    def chunk_document(self, text: str, metadata: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """Chunk policy document by legal sections and rules."""
        chunks = []
        
        # Identify policy sections using legal patterns
        sections = self._identify_policy_sections(text)
        
        for i, section in enumerate(sections):
            # Extract rules from each section
            rules = self._extract_rules_from_section(section)
            
            for j, rule in enumerate(rules):
                chunk = {
                    'content': rule['text'],
                    'chunk_index': len(chunks),
                    'chunk_type': 'policy_rule',
                    'rule_type': rule['type'],
                    'severity': rule['severity'],
                    'section_title': section.get('title', f'Section {i+1}'),
                    'applies_to': rule.get('applies_to', []),
                    'size': len(rule['text']),
                    'start_position': rule.get('start_pos', 0),
                    'end_position': rule.get('end_pos', len(rule['text'])),
                    'quality_score': self._calculate_rule_quality(rule),
                    'has_overlap': False,
                    'overlap_size': 0,
                    'embedding_ready': True
                }
                chunks.append(chunk)
        
        return chunks
    
    def _identify_policy_sections(self, text: str) -> List[Dict[str, Any]]:
        """Identify policy sections using legal document patterns."""
        sections = []
        
        # Common policy section patterns
        section_patterns = [
            r'(?i)^(\d+\.?\s*[A-Z][^.]*(?:POLICY|PROCEDURE|REQUIREMENT|GUIDELINE)[^.]*)',
            r'(?i)^([A-Z][^.]*(?:SHALL|MUST|REQUIRED|MANDATORY)[^.]*)',
            r'(?i)^(SECTION\s+\d+[^:]*:?[^.]*)',
            r'(?i)^(\d+\.\d+\s+[A-Z][^.]*)'
        ]
        
        lines = text.split('\n')
        current_section = {'title': 'Introduction', 'content': '', 'start_line': 0}
        
        for i, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue

            # Real, confirmed bug found live: section_patterns[1]
            # (SHALL|MUST|REQUIRED|MANDATORY) matches ANY line starting
            # with a capital letter that contains one of those words
            # anywhere before the first period - which describes nearly
            # every real policy RULE sentence, not just headers (that's
            # what makes it policy language in the first place). Every
            # real rule sentence that happened to follow a genuine header
            # was itself misidentified as the START of a new section,
            # scattering real rule content into orphaned title-only
            # fragments and destroying the actual rule text. Headers are
            # short labels, not full sentences - gating all 4 patterns on
            # "looks like a title" (short, not sentence-terminated) before
            # even attempting them fixes this without weakening real
            # header detection (SECTION 3:, N.N headings, etc. are still
            # short and still match).
            looks_like_header = len(line) <= 100 and not line.endswith(('.', '!', '?'))

            # Check if line matches section pattern
            is_section_header = False
            if looks_like_header:
                for pattern in section_patterns:
                    if re.match(pattern, line):
                        # Save previous section
                        if current_section['content']:
                            sections.append(current_section)

                        # Start new section
                        current_section = {
                            'title': line,
                            'content': '',
                            'start_line': i
                        }
                        is_section_header = True
                        break

            if not is_section_header:
                current_section['content'] += line + '\n'
        
        # Add final section
        if current_section['content']:
            sections.append(current_section)
        
        return sections
    
    def _extract_rules_from_section(self, section: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract individual rules from policy section."""
        rules = []
        content = section['content']
        
        # Rule patterns (mandatory, recommended, prohibited)
        rule_patterns = {
            'mandatory': [
                r'(?i)(.*(?:shall|must|required|mandatory)[^.]*\.)',
                r'(?i)(.*(?:will|is required to)[^.]*\.)',
                r'(?i)(.*(?:obligation|duty)[^.]*\.)'
            ],
            'recommended': [
                r'(?i)(.*(?:should|recommended|advised)[^.]*\.)',
                r'(?i)(.*(?:best practice|guideline)[^.]*\.)'
            ],
            'prohibited': [
                r'(?i)(.*(?:shall not|must not|prohibited|forbidden)[^.]*\.)',
                r'(?i)(.*(?:not permitted|not allowed)[^.]*\.)'
            ]
        }
        
        sentences = re.split(r'(?<=[.!?])\s+', content)
        
        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) < 20:  # Skip very short sentences
                continue
            
            rule_type = 'general'
            severity = 'MEDIUM'
            
            # Determine rule type and severity
            for rtype, patterns in rule_patterns.items():
                for pattern in patterns:
                    if re.search(pattern, sentence):
                        rule_type = rtype
                        severity = self._determine_severity(sentence, rtype)
                        break
                if rule_type != 'general':
                    break
            
            # Extract applicable contract types
            applies_to = self._extract_applicable_types(sentence)
            
            rule = {
                'text': sentence,
                'type': rule_type,
                'severity': severity,
                'applies_to': applies_to,
                'start_pos': content.find(sentence),
                'end_pos': content.find(sentence) + len(sentence)
            }
            rules.append(rule)
        
        return rules
    
    def _determine_severity(self, text: str, rule_type: str) -> str:
        """Determine rule severity based on content and type."""
        text_lower = text.lower()
        
        # Critical indicators
        if any(word in text_lower for word in ['critical', 'essential', 'vital', 'breach', 'violation']):
            return 'CRITICAL'
        
        # High severity for mandatory prohibitions
        if rule_type == 'prohibited' or any(word in text_lower for word in ['liability', 'termination', 'confidential']):
            return 'HIGH'
        
        # Medium for mandatory requirements
        if rule_type == 'mandatory':
            return 'MEDIUM'
        
        return 'LOW'
    
    def _extract_applicable_types(self, text: str) -> List[str]:
        """Extract contract types this rule applies to."""
        text_lower = text.lower()
        contract_types = []
        
        type_keywords = {
            'liability': ['liability', 'damages', 'indemnification'],
            'termination': ['termination', 'end', 'expire', 'cancel'],
            'payment': ['payment', 'invoice', 'fee', 'cost', 'price'],
            'confidentiality': ['confidential', 'proprietary', 'secret', 'nda'],
            'intellectual_property': ['ip', 'intellectual property', 'patent', 'copyright'],
            'data_protection': ['data', 'privacy', 'gdpr', 'personal information']
        }
        
        for contract_type, keywords in type_keywords.items():
            if any(keyword in text_lower for keyword in keywords):
                contract_types.append(contract_type)
        
        return contract_types if contract_types else ['general']
    
    def _calculate_rule_quality(self, rule: Dict[str, Any]) -> float:
        """Calculate quality score for extracted rule."""
        score = 0.5  # Base score
        
        # Higher score for specific rule types
        if rule['type'] in ['mandatory', 'prohibited']:
            score += 0.3
        
        # Higher score for critical/high severity
        if rule['severity'] in ['CRITICAL', 'HIGH']:
            score += 0.2
        
        # Higher score for rules with specific applicability
        if rule['applies_to'] and 'general' not in rule['applies_to']:
            score += 0.1
        
        # Text quality indicators
        text = rule['text'].lower()
        if any(word in text for word in ['shall', 'must', 'required', 'prohibited']):
            score += 0.1
        
        return min(score, 1.0)