import warnings
import torch

from transformers import PreTrainedModel
from transformers.utils import ModelOutput

"""
An adapter class for HuggingFace Generation API compatibility.

It requires model to have a forward interface as:

    forward(input_ids: Tensor(batch_size, seq_len), cache_ids: Tensor(seq_len), 
        start_ids: Optional[Tensor(batch_size)])) -> Tensor(batch_size, vocab_size)
"""
class HuggingFaceGenerationModelAdapter(PreTrainedModel):
    
    def __init__(self, config, model):
        super().__init__(config)
        self.model = model
        self.config = config
        self.cur_len = 0
        self.do_context_encode = False
        self.start_ids_after_pad = None

    def reset_generation(self):
        self.cur_len = 0
        self.do_context_encode = False
        self.start_ids_after_pad = None

    def forward(self, input_ids, cache_ids, start_ids=None, output_hidden_states=False, output_attentions=False,
            attention_mask=None, return_dict=False):
        
        if  output_hidden_states or output_attentions or attention_mask is not None:
            warnings.warn("Warning: These arguments are not used by forward(): \
                (output_hidden_states, output_attentions, attention_mask)")

        # TODO: remove this check after making forward api generalizez for serial/paralle context encoding
        if  self.do_context_encode:
            out_logits = self.model(input_ids, cache_ids, start_ids, is_context_encode=True)
            self.do_context_encode = False
        else:
            out_logits = self.model(input_ids, cache_ids, start_ids)

        out_logits = out_logits[:, None, :]
        if return_dict:
            return ModelOutput(
                [("logits", out_logits), ("past_key_values", tuple())],
            )
        return (out_logits,)

    # implemented for beam search
    # we ignore past as we don't expose k/v_cache
    def _reorder_cache(self, past, beam_idx):
        assert hasattr(self.model, 'reorder_cache') and callable(self.model.reorder_cache), f"{self.model.__class__.__name__} doesn't have reorder_cache implemented for beam search"
        self.model.reorder_cache(beam_idx)

    def prepare_inputs_for_generation(self, input_ids, past_key_values=None, inputs_embeds=None, **kwargs):
        # convert attention_mask to start_ids
        attention_mask = None
        start_ids = None
        if "attention_mask" in kwargs:
            attention_mask = kwargs["attention_mask"]

        if attention_mask is not None:
            _, start_ids = attention_mask.max(axis=1)

        if self.cur_len > 0:
            input_ids = input_ids[:, -1:]
            cache_ids = torch.as_tensor([self.cur_len], dtype=torch.int32)
            if self.start_ids_after_pad is not None:
                start_ids = self.start_ids_after_pad 
        else:
            # TODO: remove this check after making forward api generalized for both serial/paralle context encoding
            if hasattr(self.model, "context_buckets") and hasattr(self.model, "pad_context"):
                # pad input_ids and start_ids
                input_ids, start_ids, offset = self.model.pad_context(input_ids, start_ids=start_ids)
                self.do_context_encode = True
                self.start_ids_after_pad = start_ids
                # update cache_ids
                print(f"Warning: the padding offset is {offset}, make sure max_length is maller than offset+n_positions")
            cache_ids = torch.arange(input_ids.shape[-1], dtype=torch.int32)
        self.cur_len += input_ids.shape[-1] 
        model_inputs = {
            "input_ids": input_ids,
            "cache_ids": cache_ids,
            "start_ids": start_ids,
        }

        return model_inputs
