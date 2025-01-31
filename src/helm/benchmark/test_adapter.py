import shutil
import tempfile
from typing import List

from helm.common.authentication import Authentication
from helm.common.tokenization_request import TokenizationToken
from helm.proxy.services.server_service import ServerService
from .scenarios.scenario import CORRECT_TAG, create_scenario, Instance, Reference
from .run_specs import get_scenario_spec1, get_adapter_spec1
from .adapter import (
    ADAPT_GENERATION,
    ADAPT_LANGUAGE_MODELING,
    ADAPT_MULTIPLE_CHOICE_JOINT,
    Adapter,
    AdapterSpec,
    Prompt,
    Processor,
)
from .window_services.tokenizer_service import TokenizerService


DEFAULT_MODEL = "openai/davinci"


class TestAdapter:
    def setup_method(self):
        self.path: str = tempfile.mkdtemp()
        service = ServerService(base_path=self.path, root_mode=True)
        self.tokenizer_service = TokenizerService(service, Authentication("test"))

    def teardown_method(self, _):
        shutil.rmtree(self.path)

    def test_adapter1(self):
        scenario = create_scenario(get_scenario_spec1())
        adapter_spec = get_adapter_spec1()
        scenario_state = Adapter(adapter_spec, self.tokenizer_service).adapt(scenario.get_instances(), parallelism=1)

        # Make sure we generated the right number of request_states:
        # For each trial, instance and reference (+ 1 for free-form generation).
        num_instances = len(scenario_state.instances)
        assert num_instances * adapter_spec.num_train_trials == len(scenario_state.request_states)

    def test_construct_prompt(self):
        adapter_spec = AdapterSpec(
            model=DEFAULT_MODEL,
            method=ADAPT_GENERATION,
            input_prefix="",
            input_suffix="",
            output_prefix="",
            output_suffix="",
            max_tokens=100,
        )
        adapter = Adapter(adapter_spec, self.tokenizer_service)
        processor = Processor(adapter_spec, adapter.window_service, train_instances=[], train_trial_index=0)
        correct_reference = Reference(output="", tags=[CORRECT_TAG])
        train_instances: List[Instance] = [Instance(input="train", references=[correct_reference]) for _ in range(2049)]
        eval_instances = Instance(input="eval", references=[])
        prompt: Prompt = processor.construct_prompt(
            train_instances, eval_instances, include_output=False, reference_index=None
        )
        prompt_text: str = prompt.text

        # Ensure the prompt fits within the context window
        assert adapter.window_service.fits_within_context_window(prompt_text)

        # Ensure the in-context examples were removed before touching the evaluation instance
        assert prompt_text.endswith("eval")

    def test_construct_prompt_with_truncation(self):
        adapter_spec = AdapterSpec(
            model=DEFAULT_MODEL, method=ADAPT_GENERATION, input_prefix="", output_prefix="", max_tokens=100
        )
        adapter = Adapter(adapter_spec, self.tokenizer_service)
        processor = Processor(adapter_spec, adapter.window_service, train_instances=[], train_trial_index=0)
        correct_reference = Reference(output="", tags=[CORRECT_TAG])
        train_instances: List[Instance] = [Instance(input="train", references=[correct_reference]) for _ in range(100)]
        eval_instances = Instance(input="eval" * 2049, references=[])
        prompt: Prompt = processor.construct_prompt(
            train_instances, eval_instances, include_output=False, reference_index=None
        )
        prompt_text: str = prompt.text

        # Ensure the prompt fits within the context window
        assert adapter.window_service.fits_within_context_window(prompt_text)

        # Ensure that all the in-context examples were completely removed and we had to truncate the eval Instance input
        assert "train" not in prompt_text
        assert prompt_text.count("eval") == 1948

    def test_construct_language_modeling_prompt(self):
        adapter_spec = AdapterSpec(
            method=ADAPT_LANGUAGE_MODELING,
            input_prefix="",
            model=DEFAULT_MODEL,
            output_prefix="",
            max_tokens=0,
        )
        adapter = Adapter(adapter_spec, self.tokenizer_service)

        # The tokens translate to: '�Excuse me�'
        conditioning_tokens: List[TokenizationToken] = [TokenizationToken(110), TokenizationToken(40127)]
        pred_tokens: List[TokenizationToken] = [TokenizationToken(1904), TokenizationToken(502), TokenizationToken(447)]
        prompt, num_conditioning_tokens = adapter.construct_language_modeling_prompt(
            conditioning_tokens=conditioning_tokens, pred_tokens=pred_tokens, max_req_len=5, text=""
        )

        # Ensure the prompt is correct
        assert prompt == "Excuse me"

        # Ensure the number of conditioning tokens is correct
        assert num_conditioning_tokens == 1

    def test_sample_examples(self):
        adapter_spec = AdapterSpec(method=ADAPT_MULTIPLE_CHOICE_JOINT, model=DEFAULT_MODEL, max_train_instances=4)
        adapter = Adapter(adapter_spec, self.tokenizer_service)
        all_train_instances = [
            Instance("say no", references=[Reference("no", tags=[CORRECT_TAG])]),
            Instance("say yes1", references=[Reference("yes", tags=[CORRECT_TAG])]),
            Instance("say yes2", references=[Reference("yes", tags=[CORRECT_TAG])]),
            Instance("say yes3", references=[Reference("yes", tags=[CORRECT_TAG])]),
            Instance("say yes4", references=[Reference("yes", tags=[CORRECT_TAG])]),
        ]

        examples = adapter.sample_examples(all_train_instances, seed=0)
        assert len(examples) == 4

        # An instance with "say yes" should have be sampled first before "say no"
        assert examples[0].input == "say yes4"
        assert examples[1].input == "say no"
        assert examples[2].input == "say yes1"
        assert examples[3].input == "say yes3"

    def test_sample_examples_no_train_instances(self):
        adapter_spec = AdapterSpec(method=ADAPT_MULTIPLE_CHOICE_JOINT, model=DEFAULT_MODEL, max_train_instances=2)
        adapter = Adapter(adapter_spec, self.tokenizer_service)
        examples = adapter.sample_examples(all_train_instances=[], seed=0)
        assert len(examples) == 0

    def test_sample_examples_greater_max_train_instances(self):
        adapter_spec = AdapterSpec(method=ADAPT_MULTIPLE_CHOICE_JOINT, model=DEFAULT_MODEL, max_train_instances=10)
        adapter = Adapter(adapter_spec, self.tokenizer_service)
        all_train_instances = [
            Instance("say no", references=[Reference("no", tags=[CORRECT_TAG])]),
            Instance("say yes", references=[Reference("yes", tags=[CORRECT_TAG])]),
            Instance("say yes", references=[Reference("yes", tags=[CORRECT_TAG])]),
        ]

        examples = adapter.sample_examples(all_train_instances, seed=0)
        assert len(examples) == 3

    def test_sample_examples_without_references(self):
        adapter_spec = AdapterSpec(method=ADAPT_LANGUAGE_MODELING, model=DEFAULT_MODEL, max_train_instances=1)
        adapter = Adapter(adapter_spec, self.tokenizer_service)
        all_train_instances = [
            Instance("prompt1", references=[]),
            Instance("prompt2", references=[]),
            Instance("prompt3", references=[]),
        ]

        examples = adapter.sample_examples(all_train_instances, seed=0)
        assert len(examples) == 1

    def test_sample_examples_open_ended_generation(self):
        adapter_spec = AdapterSpec(model=DEFAULT_MODEL, max_train_instances=3)
        adapter = Adapter(adapter_spec, self.tokenizer_service)

        all_train_instances: List[Instance] = [
            Instance(f"prompt{i}", references=[Reference(f"reference{i}", tags=[CORRECT_TAG])]) for i in range(1, 10)
        ]
        seed0_examples: List[Instance] = adapter.sample_examples(all_train_instances, seed=0)
        seed1_examples: List[Instance] = adapter.sample_examples(all_train_instances, seed=1)

        assert len(seed0_examples) == len(seed1_examples) == 3
        assert seed0_examples != seed1_examples, "Examples should differ when changing the seed"

    def test_sample_examples_open_ended_generation_stress(self):
        adapter_spec = AdapterSpec(model=DEFAULT_MODEL, max_train_instances=5)
        adapter = Adapter(adapter_spec, self.tokenizer_service)

        all_train_instances: List[Instance] = [
            Instance("prompt3", references=[Reference("reference3", tags=[CORRECT_TAG])]),
            Instance("prompt3", references=[Reference("reference3", tags=[CORRECT_TAG])]),
            Instance("prompt1", references=[Reference("reference1", tags=[CORRECT_TAG])]),
            Instance("prompt1", references=[Reference("reference1", tags=[CORRECT_TAG])]),
            Instance("prompt1", references=[Reference("reference1", tags=[CORRECT_TAG])]),
            Instance("prompt2", references=[Reference("reference2", tags=[CORRECT_TAG])]),
            Instance("prompt2", references=[Reference("reference2", tags=[CORRECT_TAG])]),
        ]
        # Add prompt4,..,prompt100
        for i in range(4, 100):
            all_train_instances.append(
                Instance(f"prompt{i}", references=[Reference(f"reference{i}", tags=[CORRECT_TAG])]),
            )

        previous_train_instances: List[List[Instance]] = []
        for seed in range(10):
            train_instances = adapter.sample_examples(all_train_instances, seed=seed)
            # Ensure calling the method with the same seed again picks the same train Instances
            assert train_instances == adapter.sample_examples(all_train_instances, seed=seed)

            assert len(train_instances) == 5
            assert train_instances[0].input == "prompt1", "prompt1 Instance had the most common label: reference1"
            assert train_instances[1].input in ["prompt2", "prompt3"]
            assert train_instances[2].input in ["prompt2", "prompt3"]
            assert train_instances[3].input not in ["prompt1", "prompt2", "prompt3"]
            assert train_instances[4].input not in ["prompt1", "prompt2", "prompt3"]

            # Ensure we haven't seen the same in-context examples before from previous seeds
            for other_train_instances in previous_train_instances:
                assert train_instances != other_train_instances, "Examples should differ when changing the seed"
            previous_train_instances.append(train_instances)

    def test_fits_tokens_within_context_window(self):
        adapter_spec = AdapterSpec(
            method=ADAPT_LANGUAGE_MODELING,
            input_prefix="",
            model=DEFAULT_MODEL,
            output_prefix="",
            max_tokens=0,
        )
        adapter = Adapter(adapter_spec, self.tokenizer_service)

        # The tokens translate to: '<|endoftext|>The the the the ... the the'
        # There are 1 `conditioning_token` and 2049 `pred_tokens`. Since the `max_request_length`
        # of GPT-3 is 2049, calling `fits_tokens_within_context_window` will remove the last `pred_token`
        conditioning_tokens: List[TokenizationToken] = [TokenizationToken(50256)]
        pred_tokens: List[TokenizationToken] = [TokenizationToken(464)] + [TokenizationToken(262)] * 2048
        prompt, pred_tokens = adapter.fits_tokens_within_context_window(
            conditioning_tokens, pred_tokens, adapter.window_service.max_request_length
        )

        # Ensure the prompt is correct
        assert prompt == "<|endoftext|>The" + " the" * 2047

        # Ensure the pred_tokens are correct
        assert pred_tokens == [TokenizationToken(464)] + [TokenizationToken(262)] * 2047
