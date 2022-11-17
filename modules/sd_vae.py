import torch
import os
from collections import namedtuple
from modules import shared, devices, script_callbacks
from modules.paths import models_path
import glob
from typing import Union
from copy import deepcopy


model_dir = "Stable-diffusion"
model_path = os.path.abspath(os.path.join(models_path, model_dir))
vae_dir = "VAE"
vae_path = os.path.abspath(os.path.join(models_path, vae_dir))


vae_ignore_keys = {"model_ema.decay", "model_ema.num_updates"}


default_vae_dict = {"auto": "auto", "None": "None"}
default_vae_list = ["auto", "None"]


default_vae_values = [default_vae_dict[x] for x in default_vae_list]
vae_dict = dict(default_vae_dict)
vae_list = list(default_vae_list)
first_load = True


base_vae        = None  # cached bundled vae weight in main memory, ONLY when externel vae is to load
is_base_vae     = True  # is current vae the bundled one?
loaded_vae_file = None  # path to current external vae file
checkpoint_info = None  # ckpt for cached bunlded vae to match


def store_base_vae(model):
    global base_vae, checkpoint_info
    base_vae = deepcopy(model.first_stage_model.state_dict())
    checkpoint_info = model.sd_checkpoint_info


def restore_base_vae(model):
    global base_vae, checkpoint_info
    # load it, if bundled vae is cached and ckpt matches
    if base_vae is not None and checkpoint_info == model.sd_checkpoint_info:
        _load_vae_dict(model, base_vae)

        # safely unload from memory
        base_vae = None


def _load_vae_dict(model, vae_dict):
    assert vae_dict
    model.first_stage_model.load_state_dict(vae_dict)
    model.first_stage_model.to(devices.dtype_vae)


def get_filename(filepath):
    return os.path.splitext(os.path.basename(filepath))[0]


def refresh_vae_list(vae_path=vae_path, model_path=model_path):
    global vae_dict, vae_list
    res = {}
    candidates = [
        *glob.iglob(os.path.join(model_path, '**/*.vae.ckpt'), recursive=True),
        *glob.iglob(os.path.join(model_path, '**/*.vae.pt'), recursive=True),
        *glob.iglob(os.path.join(vae_path, '**/*.ckpt'), recursive=True),
        *glob.iglob(os.path.join(vae_path, '**/*.pt'), recursive=True)
    ]
    if shared.cmd_opts.vae_path is not None and os.path.isfile(shared.cmd_opts.vae_path):
        candidates.append(shared.cmd_opts.vae_path)
    for filepath in candidates:
        name = get_filename(filepath)
        res[name] = filepath
    vae_list.clear()
    vae_list.extend(default_vae_list)
    vae_list.extend(list(res.keys()))
    vae_dict.clear()
    vae_dict.update(res)
    vae_dict.update(default_vae_dict)
    return vae_list


def resolve_vae(checkpoint_file, vae_file="auto") -> Union[str, None]:
    global first_load, vae_dict, vae_list

    # if vae_file argument is provided, it takes priority, but not saved
    if vae_file and vae_file not in default_vae_list:
        if not os.path.isfile(vae_file):
            vae_file = "auto"
            print("VAE provided as function argument doesn't exist")
    # for the first load, if vae-path is provided, it takes priority, saved, and failure is reported
    if first_load and shared.cmd_opts.vae_path is not None:
        if os.path.isfile(shared.cmd_opts.vae_path):
            vae_file = shared.cmd_opts.vae_path
            shared.opts.data['sd_vae'] = get_filename(vae_file)
        else:
            print("VAE provided as command line argument doesn't exist")
    # else, we load from settings
    if vae_file == "auto" and shared.opts.sd_vae is not None:
        # if saved VAE settings isn't recognized, fallback to auto
        vae_file = vae_dict.get(shared.opts.sd_vae, "auto")
        # if VAE selected but not found, fallback to auto
        if vae_file not in default_vae_values and not os.path.isfile(vae_file):
            vae_file = "auto"
            print("Selected VAE doesn't exist")
    # vae-path cmd arg takes priority for auto
    if vae_file == "auto" and shared.cmd_opts.vae_path is not None:
        if os.path.isfile(shared.cmd_opts.vae_path):
            vae_file = shared.cmd_opts.vae_path
            print("Using VAE provided as command line argument")
    # if still not found, try look for ".vae.pt" beside model
    model_path = os.path.splitext(checkpoint_file)[0]
    if vae_file == "auto":
        vae_file_try = model_path + ".vae.pt"
        if os.path.isfile(vae_file_try):
            vae_file = vae_file_try
            print("Using VAE found beside selected model")
    # if still not found, try look for ".vae.ckpt" beside model
    if vae_file == "auto":
        vae_file_try = model_path + ".vae.ckpt"
        if os.path.isfile(vae_file_try):
            vae_file = vae_file_try
            print("Using VAE found beside selected model")
    # No more fallbacks for auto
    if vae_file == "auto":
        vae_file = None
    # Last check, just because
    if vae_file and not os.path.exists(vae_file):
        vae_file = None

    return vae_file


def load_vae(model, vae_file=None):
    global first_load, vae_dict, vae_list, loaded_vae_file
    global base_vae, is_base_vae, checkpoint_info
    # save_settings = False

    # when ckpt changed, mark using bundled vae & clean cache
    if checkpoint_info != model.sd_checkpoint_info:
        checkpoint_info = None
        base_vae        = None
        is_base_vae     = True

    # load externel vae file 
    if vae_file:
        # is current is the bundled, cache it to memory (lazy cache!)
        if is_base_vae: store_base_vae(model)

        print(f"Loading VAE weights from: {vae_file}")
        vae_ckpt = torch.load(vae_file, map_location=shared.weight_load_location)
        vae_dict_1 = {k: v for k, v in vae_ckpt["state_dict"].items() if k[0:4] != "loss" and k not in vae_ignore_keys}
        _load_vae_dict(model, vae_dict_1)
        is_base_vae = False

        # If vae used is not in dict, update it
        # It will be removed on refresh though
        vae_opt = get_filename(vae_file)
        if vae_opt not in vae_dict:
            vae_dict[vae_opt] = vae_file
            vae_list.append(vae_opt)
    # revert to the cached bundle vae
    else:
        restore_base_vae(model)
        is_base_vae = True

    loaded_vae_file = vae_file

    """
    # Save current VAE to VAE settings, maybe? will it work?
    if save_settings:
        if vae_file is None:
            vae_opt = "None"

        # shared.opts.sd_vae = vae_opt
    """

    first_load = False


def reload_vae_weights(sd_model=None, vae_file="auto"):
    from modules import lowvram, devices, sd_hijack

    if not sd_model:
        sd_model = shared.sd_model

    checkpoint_info = sd_model.sd_checkpoint_info
    checkpoint_file = checkpoint_info.filename
    vae_file = resolve_vae(checkpoint_file, vae_file=vae_file)

    if loaded_vae_file == vae_file:
        return

    if shared.cmd_opts.lowvram or shared.cmd_opts.medvram:
        lowvram.send_everything_to_cpu()
    else:
        sd_model.to(devices.cpu)

    sd_hijack.model_hijack.undo_hijack(sd_model)

    load_vae(sd_model, vae_file)

    sd_hijack.model_hijack.hijack(sd_model)
    script_callbacks.model_loaded_callback(sd_model)

    if not shared.cmd_opts.lowvram and not shared.cmd_opts.medvram:
        sd_model.to(devices.device)

    print(f"VAE Weights loaded.")
    return sd_model
