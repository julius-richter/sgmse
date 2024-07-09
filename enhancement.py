import glob
import torch
from tqdm import tqdm
from os import makedirs
from soundfile import write
from torchaudio import load
from os.path import join, dirname
from argparse import ArgumentParser

# Set CUDA architecture list
from sgmse.util.other import set_torch_cuda_arch_list
set_torch_cuda_arch_list()

from sgmse.model import ScoreModel
from sgmse.util.other import pad_spec


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument("--test_dir", type=str, required=True, help='Directory containing the test data')
    parser.add_argument("--enhanced_dir", type=str, required=True, help='Directory containing the enhanced data')
    parser.add_argument("--ckpt", type=str,  help='Path to model checkpoint')
    parser.add_argument("--corrector", type=str, choices=("ald", "langevin", "none"), default="ald", help="Corrector class for the PC sampler.")
    parser.add_argument("--corrector_steps", type=int, default=1, help="Number of corrector steps")
    parser.add_argument("--snr", type=float, default=0.5, help="SNR value for (annealed) Langevin dynmaics")
    parser.add_argument("--N", type=int, default=30, help="Number of reverse steps")
    parser.add_argument("--device", type=str, default="cuda", help="Device to use for inference")
    args = parser.parse_args()

    # Load score model 
    model = ScoreModel.load_from_checkpoint(args.ckpt, map_location=args.device)
    model.eval()

    # Get list of noisy files
    noisy_files = []
    noisy_files += sorted(glob.glob(join(args.test_dir, '*.wav')))
    noisy_files += sorted(glob.glob(join(args.test_dir, '**', '*.wav')))

    # Check if the model is trained on 48 kHz data
    if model.backbone == 'ncsnpp_48k':
        sr = 48000
        pad_mode = "reflection"
    else:
        sr = 16000
        pad_mode = "zero_pad"

    # Enhance files
    for noisy_file in tqdm(noisy_files):
        filename = noisy_file.split('/')[-1]
        filename = noisy_file.replace(args.test_dir, "")[1:] # Remove the first character which is a slash
        
        # Load wav
        y, _ = load(noisy_file) 
        T_orig = y.size(1)   

        # Normalize
        norm_factor = y.abs().max()
        y = y / norm_factor
        
        # Prepare DNN input
        Y = torch.unsqueeze(model._forward_transform(model._stft(y.to(args.device))), 0)
        Y = pad_spec(Y, mode=pad_mode)
        
        # Reverse sampling
        sampler = model.get_pc_sampler(
            'reverse_diffusion', args.corrector, Y.to(args.device), N=args.N, 
            corrector_steps=args.corrector_steps, snr=args.snr)
        sample, _ = sampler()
        
        # Backward transform in time domain
        x_hat = model.to_audio(sample.squeeze(), T_orig)

        # Renormalize
        x_hat = x_hat * norm_factor

        # Write enhanced wav file
        makedirs(dirname(join(args.enhanced_dir, filename)), exist_ok=True)
        write(join(args.enhanced_dir, filename), x_hat.cpu().numpy(), sr)
