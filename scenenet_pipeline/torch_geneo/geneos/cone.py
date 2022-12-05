
# %%
import itertools
import time
from matplotlib import cm
import numpy as np
import sympy as smp
import sympy.vector as smpv
import sympy.physics.vector as spv
import sympytorch as spt
from scipy import integrate as intg
from sklearn.metrics import precision_score, recall_score, f1_score

import IPython.display as disp
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D
import torch.nn.functional as F
import torch


import sys
sys.path.insert(0, '..')
sys.path.insert(1, '../..')
from VoxGENEO import Voxelization as Vox
from EDA import EDA_utils as eda
from torch_geneo.datasets.ts40k import ToTensor, torch_TS40K
from torch_geneo.geneos.GENEO_kernel_torch import GENEO_kernel_torch


class cone_kernel(GENEO_kernel_torch):

    def __init__(self, name, kernel_size, plot=False, **kwargs):
        """
        GENEO kernel that encodes a cone on top of a cylinder.\n

        The cylinder's radius and cone's apex are required to
        compute it

        Required
        --------
        `radius` - float \in ]0, kernel_size[1]] :
        radius of the cylinder's base;

        `apex` - float \in [0, kernel_size[0]]:
        cone's apex point relative to the height of the kernel; 

        `cone_radius` - float \in  ]0, kernel_size[1]]:
        cone's base radius

        `cone_inc` - float \in ]0, 1[
        cone's inclination

        Optional
        --------
        `epsilon` - float \in [0, 1]:
        threshold below and beyond the radius to consider in the pattern's definition.
        x^2 + y^2 <= (radius + epsilon)^2 && x^2 + y^2 >= (radius - epsilon)^2;

        `sigma` - float:
        sigma variable for the gaussian distribution when assigning weights to the kernel;

        `tau` - float \in [0, 1]:
        Detection threshold for GENEO prediction score
        """

        if kwargs.get('radius') is None:
            raise KeyError("Provide a radius for the cylinder in the kernel.")
        
        if kwargs.get('apex') is None:
            raise KeyError("Provide a height for the cone.")

        if kwargs.get('cone_inc') is None:
            raise KeyError("Provide an inclination for the cone.")

        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')


        self.radius = kwargs['radius'].to(self.device)
        self.apex = kwargs['radius'].to(self.device)
        self.cone_inc = kwargs['cone_inc'].to(self.device)


        if kwargs.get('cone_radius') is None:
            self.cone_radius = torch.tensor(kernel_size[1]-1, device=self.device)
        else:
            self.cone_radius = kwargs['cone_radius'].to(self.device)
        
        if plot:
            print("--- Cone Kernel ---")
            print(f"radius = {self.radius:.4f}; {type(self.radius)};")
            print(f"apex = {self.apex:.4f}; {type(self.apex)};")
            print(f"cone_radius = {self.cone_radius:.4f}; {type(self.cone_radius)};")
            print(f"cone_inc = {self.cone_inc:.4f}; {type(self.cone_inc)};")

        self.sigma = 1
        if kwargs.get('sigma') is not None:
            self.sigma = kwargs['sigma']
            if plot:
                print(f"sigma = {self.sigma:.4f}; {type(self.sigma)};")           

        self.epsilon = 0.2
        if kwargs.get('epsilon') is not None:
            self.epsilon = kwargs['epsilon']
            if plot: 
                print(f"epsilon = {self.epsilon:.4f}; {type(self.epsilon)};")
        
        super().__init__(name, kernel_size, plot)
    
    def mandatory_parameters():
        return ['radius', 'apex', 'cone_radius','cone_inc']

    def geneo_parameters():
        return cone_kernel.mandatory_parameters() + ['sigma']

    def geneo_random_config(name='GENEO_rand'):
        rand_config = GENEO_kernel_torch.geneo_random_config()

        # rand_config['kernel_size'] = (9, 9, 9)

        k_size = rand_config['kernel_size']

        geneo_params = {
            'radius' : torch.randint(1, k_size[1], (1,))[0] / 2 ,
            'apex': torch.randint(0, k_size[0]-1, (1,))[0],
            'cone_radius' : torch.randint(1, k_size[1], (1,))[0] / 2,
            'cone_inc' : torch.rand(1,)[0],
            'sigma' : torch.randint(5, 10, (1,))[0] / 5 #float \in [1, 2]
        }

        rand_config['geneo_params'] = geneo_params

        rand_config['name'] = 'cone'

        rand_config['non_trainable'] = ['apex']
        return rand_config

    def geneo_smart_config(name="Smart_Cylinder"):

        config = {
            'name' : name,
            'kernel_size': (9, 6, 6),
            'plot': False,
            'non_trainable' : [],
            
            'geneo_params' : {
                                'radius' :  torch.tensor(1.0) ,
                                'apex':  torch.tensor(3.0),
                                'cone_radius' :  torch.tensor(2.0),
                                'cone_inc' :  torch.tensor(0.1),
                                'sigma' :  torch.tensor(2.0)
                             }
        }

        return config


    def gaussian(self, x:torch.Tensor, rad=None, sig=None, epsilon=0) -> torch.Tensor:
        center = torch.tensor([(self.kernel_size[1]-1)/2, (self.kernel_size[2]-1)/2], dtype=torch.float, device=self.device, requires_grad=True)
        x_c = x - center # Nx2
        if rad is None:
            rad = self.radius
        if sig is None:
            sig = self.sigma
        x_c_norm = torch.linalg.norm(x_c, dim=1, keepdim=True) # Nx1
        circle_x = x_c_norm**2 - (rad + epsilon)**2 

        return torch.exp((circle_x**2)*(-1 / (2*sig**2)))

    def sum_zero(self, tensor:torch.Tensor) -> torch.Tensor:
        return tensor - torch.sum(tensor) / torch.prod(torch.tensor(self.kernel_size[1:])) 
        
    def compute_kernel(self, plot=False):

        floor_idxs = torch.stack(
                        torch.meshgrid(torch.arange(self.kernel_size[1], dtype=torch.float, device=self.device, requires_grad=True), 
                                    torch.arange(self.kernel_size[2], dtype=torch.float, device=self.device, requires_grad=True))
                    ).T.reshape(-1, 2)

        cylinder_vals = self.gaussian(floor_idxs)
        cylinder_vals = self.sum_zero(cylinder_vals)
        cylinder_vals = torch.t(cylinder_vals).view(self.kernel_size[1], self.kernel_size[2])     
        hc = 4
    
        kernel = torch.tile(cylinder_vals, (hc, 1, 1))

        cone_height = torch.tensor(self.kernel_size[0]) - hc

        heights = torch.arange(cone_height.item(), device=self.device, dtype=torch.long)
        c_radius_h = self.cone_radius*torch.sin(self.cone_inc*torch.pi / (2 + heights))

        for h in heights:
            height_vals = self.gaussian(floor_idxs, rad=c_radius_h[h], sig=None)
            height_vals = self.sum_zero(height_vals)
            height_vals = torch.t(height_vals).view(1, self.kernel_size[1], self.kernel_size[2])  

            kernel = torch.cat((height_vals, kernel), dim=0)

        return kernel

# %%
if __name__ == "__main__":
    import os
    from pathlib import Path
    import torch.nn as nn


    ROOT_PROJECT = str(Path(Path(os.getcwd()).parent.absolute()).parent.absolute())

    DATA_SAMPLE_DIR = ROOT_PROJECT + "/Data_sample"
    SAVE_DIR = ROOT_PROJECT + "/dataset/torch_dataset"
    print(DATA_SAMPLE_DIR)

    #build_data_samples([DATA_SAMPLE_DIR], SAVE_DIR)
    ts40k = torch_TS40K(dataset_path=SAVE_DIR, transform=ToTensor())

    vox, vox_gt = ts40k[2]
    print(vox.shape)
    # Vox.plot_voxelgrid(vox.numpy()[0])
    # Vox.plot_voxelgrid(vox_gt.numpy()[0])

    # %%

    print(cone_kernel.geneo_random_config())
    

    # %%

    cone = cone_kernel('cy', (9, 9, 9), plot = True, radius=torch.tensor(1.0),  
                                        sigma=torch.tensor(2.0), 
                                        apex=nn.Parameter(torch.tensor(0.0)), 
                                        cone_radius=torch.tensor(0.6),
                                        cone_inc=nn.Parameter(torch.tensor(0.0)))
  
    # %%
    conv = cone.convolution(vox.view((1, *vox.shape)).to(cone.device))


    # %%
    cone.visualize_kernel()

    # %%
    type(cone.kernel)

# %%
